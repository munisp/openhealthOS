// Imaging service (ADR-0028): the DICOM seam and the video pipeline front
// door. Orthanc (GPLv3) is composed as a standalone PACS container — the
// platform speaks to it over DICOMweb/REST only, never links it. This
// service adds what Orthanc does not: triage-priority worklists with SLAs,
// a de-identification export profile, and transcode-job dispatch for
// clinical video (rsmpeg worker, workers/transcoder).
//
// Analysis doctrine: triage and workflow, never autonomous diagnosis — an
// AI that diagnoses is a medical device; this rail makes the radiologist
// faster and keeps the signature human.
package main

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

// ------------------------------------------------------------- DICOM types

// Study is the worklist-facing projection of a DICOM study. The full tag
// payload stays in the PACS; we cache what triage and audit need.
type Study struct {
	StudyUID    string    `json:"study_uid"`
	PatientID   string    `json:"patient_id"`
	Modality    string    `json:"modality"` // CR, CT, MR, US...
	Description string    `json:"description"`
	StudyDate   string    `json:"study_date"`
	PACSID      string    `json:"pacs_id"` // Orthanc internal id
	ReceivedAt  time.Time `json:"received_at"`
	Priority    string    `json:"priority"` // routine | urgent | stat
	PriorityWhy string    `json:"priority_reason,omitempty"`
	ReadBy      string    `json:"read_by,omitempty"` // radiologist who signed
	ReadAt      time.Time `json:"read_at,omitempty"`
}

// STAT rules: modality + keyword combos that float a study to the top of
// the worklist. Deliberately conservative — a false STAT costs minutes of
// radiologist time; a missed STAT costs a patient.
var statKeywords = []string{"trauma", "pneumothorax", "stroke", "bleed", "haemorrhage", "hemorrhage", "fracture", "ectopic", "appendic"}

// PriorityFor derives the initial triage level from study metadata.
func PriorityFor(modality, description string) (string, string) {
	desc := strings.ToLower(description)
	for _, kw := range statKeywords {
		if strings.Contains(desc, kw) {
			return "stat", "keyword: " + kw
		}
	}
	if modality == "CR" || modality == "US" {
		return "urgent", "portable modality"
	}
	return "routine", ""
}

// priorityRank orders the worklist: stat < urgent < routine, then unread
// before read, then oldest first (FIFO inside a band).
func priorityRank(p string) int {
	switch p {
	case "stat":
		return 0
	case "urgent":
		return 1
	default:
		return 2
	}
}

// --------------------------------------------------------------- worklist

var ErrStudyNotFound = errors.New("unknown study")

type Worklist struct {
	mu      sync.Mutex
	studies map[string]*Study
}

func NewWorklist() *Worklist { return &Worklist{studies: map[string]*Study{}} }

func (w *Worklist) Register(st Study) {
	w.mu.Lock()
	defer w.mu.Unlock()
	if _, dup := w.studies[st.StudyUID]; dup {
		return // idempotent: PACS webhooks can fire twice
	}
	if st.Priority == "" {
		st.Priority, st.PriorityWhy = PriorityFor(st.Modality, st.Description)
	}
	st.ReceivedAt = time.Now()
	w.studies[st.StudyUID] = &st
}

func (w *Worklist) Prioritize(uid, level, reason, actor string) error {
	if actor == "" || reason == "" {
		return errors.New("priority changes name who and why")
	}
	if priorityRank(level) > 2 {
		return fmt.Errorf("unknown priority %q", level)
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	st, ok := w.studies[uid]
	if !ok {
		return ErrStudyNotFound
	}
	st.Priority, st.PriorityWhy = level, actor+": "+reason
	return nil
}

// Sign records the radiologist's signature — the only path from unread to
// read. The report itself lives as a FHIR DiagnosticReport with media refs.
func (w *Worklist) Sign(uid, radiologist string) error {
	if radiologist == "" {
		return errors.New("signature names the radiologist")
	}
	w.mu.Lock()
	defer w.mu.Unlock()
	st, ok := w.studies[uid]
	if !ok {
		return ErrStudyNotFound
	}
	st.ReadBy, st.ReadAt = radiologist, time.Now()
	return nil
}

// List returns the worklist ordered stat → urgent → routine, unread first,
// oldest first within a band. overdueOnly keeps only studies past their SLA.
func (w *Worklist) List(overdueOnly bool, now time.Time) []Study {
	w.mu.Lock()
	defer w.mu.Unlock()
	out := make([]Study, 0, len(w.studies))
	for _, st := range w.studies {
		if overdueOnly && !Overdue(*st, now) {
			continue
		}
		out = append(out, *st)
	}
	sort.Slice(out, func(i, j int) bool {
		pi, pj := priorityRank(out[i].Priority), priorityRank(out[j].Priority)
		if pi != pj {
			return pi < pj
		}
		ri := out[i].ReadBy != ""
		rj := out[j].ReadBy != ""
		if ri != rj {
			return !ri
		}
		return out[i].ReceivedAt.Before(out[j].ReceivedAt)
	})
	return out
}

// Read SLAs by priority band (hours). Triage targets, not diagnostics.
var readSLA = map[string]time.Duration{
	"stat":    1 * time.Hour,
	"urgent":  4 * time.Hour,
	"routine": 24 * time.Hour,
}

func Overdue(st Study, now time.Time) bool {
	if st.ReadBy != "" {
		return false
	}
	return now.Sub(st.ReceivedAt) > readSLA[st.Priority]
}

// -------------------------------------------------------- de-identification

// DeIDProfile is the tag policy applied to exports (research, referrals to
// third parties). Keep-list, not deny-list: anything not named is dropped.
// Burned-in pixel annotations cannot be removed by tag edits — exports of
// modalities that commonly burn in IDs (US, CR) carry a warning the caller
// must surface.
var deIDKeepTags = []string{
	"Modality", "StudyDate", "StudyDescription", "SeriesDescription",
	"BodyPartExamined", "ViewPosition", "PatientAge", "PatientSex",
	"KiloVoltPeak", "ExposureTime", "SliceThickness", "PixelSpacing",
}

var burnedInRiskModalities = map[string]bool{"US": true, "CR": true, "DX": true, "ES": true}

// DeIDPlan is what an export will do — computed, reviewable, logged.
type DeIDPlan struct {
	KeepTags          []string `json:"keep_tags"`
	PatientPseudonym  string   `json:"patient_pseudonym"`
	BurnedInPixelRisk bool     `json:"burned_in_pixel_risk"`
	KAnonymity        int      `json:"k_anonymity_required"`
}

const ExportKAnonymity = 5 // same doctrine as gisexport: never export cohorts smaller than 5

// PlanDeID computes the export policy for a study. cohortSize is the number
// of patients in the export batch — under k, refuse.
func PlanDeID(st Study, cohortSize int, pseudonym string) (DeIDPlan, error) {
	if cohortSize < ExportKAnonymity {
		return DeIDPlan{}, fmt.Errorf("cohort of %d patients is below k=%d anonymity floor", cohortSize, ExportKAnonymity)
	}
	if pseudonym == "" {
		return DeIDPlan{}, errors.New("exports use pseudonyms, never real patient ids")
	}
	return DeIDPlan{
		KeepTags:          append([]string{}, deIDKeepTags...),
		PatientPseudonym:  pseudonym,
		BurnedInPixelRisk: burnedInRiskModalities[st.Modality],
		KAnonymity:        ExportKAnonymity,
	}, nil
}

// ------------------------------------------------------------- video jobs

// VideoJob is the manifest the rsmpeg transcoder worker picks up.
type VideoJob struct {
	ID        string    `json:"id"`
	SourceRef string    `json:"source_ref"` // blob://media/{id} of the raw upload
	Actor     string    `json:"actor"`
	CreatedAt time.Time `json:"created_at"`
	Status    string    `json:"status"` // queued | running | done | failed
	Error     string    `json:"error,omitempty"`
	// Outputs: manifest + segment blob refs once done.
	ManifestRef string   `json:"manifest_ref,omitempty"`
	SegmentRefs []string `json:"segment_refs,omitempty"`
	Renditions  []int    `json:"renditions,omitempty"` // heights actually produced
}

type VideoQueue struct {
	mu   sync.Mutex
	seq  int
	jobs map[string]*VideoJob
	sink func(VideoJob) error // persistence/notify hook (queue dir in prod)
}

func NewVideoQueue(sink func(VideoJob) error) *VideoQueue {
	return &VideoQueue{jobs: map[string]*VideoJob{}, sink: sink}
}

// Enqueue validates the source reference and registers a job.
func (q *VideoQueue) Enqueue(sourceRef, actor string) (VideoJob, error) {
	if actor == "" {
		return VideoJob{}, errors.New("jobs name an actor")
	}
	if !strings.HasPrefix(sourceRef, "blob://media/") {
		return VideoJob{}, errors.New("source must be a blob://media/ reference from a multipart upload")
	}
	q.mu.Lock()
	q.seq++
	j := &VideoJob{ID: fmt.Sprintf("VID-%08d", q.seq), SourceRef: sourceRef,
		Actor: actor, CreatedAt: time.Now(), Status: "queued"}
	q.jobs[j.ID] = j
	q.mu.Unlock()
	if q.sink != nil {
		if err := q.sink(*j); err != nil {
			return VideoJob{}, err
		}
	}
	return *j, nil
}

// Update is the worker's callback: status transitions and outputs.
func (q *VideoQueue) Update(id, status, errStr, manifestRef string, segmentRefs []string, renditions []int) error {
	q.mu.Lock()
	defer q.mu.Unlock()
	j, ok := q.jobs[id]
	if !ok {
		return errors.New("unknown video job")
	}
	switch status {
	case "running", "done", "failed":
		j.Status = status
		j.Error = errStr
	default:
		return fmt.Errorf("illegal status %q", status)
	}
	if status == "done" {
		if manifestRef == "" {
			return errors.New("done needs a manifest ref")
		}
		j.ManifestRef = manifestRef
		j.SegmentRefs = segmentRefs
		j.Renditions = renditions
	}
	return nil
}

func (q *VideoQueue) Get(id string) (VideoJob, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	j, ok := q.jobs[id]
	if !ok {
		return VideoJob{}, errors.New("unknown video job")
	}
	return *j, nil
}
