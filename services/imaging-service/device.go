// device.go — modality worklist (MWL) + device result ingest (ADR-0052).
//
// MWL: modalities (MRI/CT/US/X-ray scanners) pull today's scheduled
// procedures per station — the DICOM MWL pattern over plain HTTP+JSON,
// since our devices speak REST at the edge and Orthanc handles real DICOM
// nodes. A device that knows what it should be doing never keys patient
// demographics by hand — the #1 cause of orphaned studies.
//
// Device ingest: point-of-care devices (vitals monitors, glucometers, lab
// analyzers, scale/BP cuffs) POST observations here; we wrap them as FHIR
// Observations and forward to the SHR. Fail-soft: SHR down → the reading
// is still recorded locally and returned 202 with forwarded=false.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// ------------------------------------------------------------- MWL

// WorklistItem is one scheduled procedure step a modality can pull.
type WorklistItem struct {
	ID          string `json:"id"`
	PatientID   string `json:"patient_id"`
	PatientName string `json:"patient_name,omitempty"`
	Modality    string `json:"modality"` // MR | CT | US | CR | DX | ECG ...
	Procedure   string `json:"procedure"`
	StationAE   string `json:"station_ae,omitempty"` // DICOM AE title of the modality
	Scheduled   string `json:"scheduled"`            // RFC3339
	Status      string `json:"status"`               // scheduled | arrived | in-progress | completed | cancelled
	Accession   string `json:"accession,omitempty"`
}

var mwlStatuses = map[string]bool{
	"scheduled": true, "arrived": true, "in-progress": true,
	"completed": true, "cancelled": true,
}

var mwlTransitions = map[string][]string{
	"scheduled":   {"arrived", "cancelled"},
	"arrived":     {"in-progress", "cancelled"},
	"in-progress": {"completed", "cancelled"},
	"completed":   {},
	"cancelled":   {},
}

// MWL is the modality worklist store.
type MWL struct {
	mu    sync.Mutex
	items map[string]*WorklistItem
	seq   int
	now   func() time.Time
}

func NewMWL(now func() time.Time) *MWL { return &MWL{items: map[string]*WorklistItem{}, now: now} }

// Schedule adds a procedure step. Modality is required — a worklist item
// without a modality can never be pulled by a device.
func (m *MWL) Schedule(it WorklistItem) (WorklistItem, error) {
	if it.PatientID == "" || it.Modality == "" || it.Procedure == "" {
		return WorklistItem{}, errors.New("patient_id, modality and procedure are required")
	}
	if it.Scheduled == "" {
		it.Scheduled = m.now().UTC().Format(time.RFC3339)
	}
	if _, err := time.Parse(time.RFC3339, it.Scheduled); err != nil {
		return WorklistItem{}, errors.New("scheduled must be RFC3339")
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	it.ID = fmt.Sprintf("MWL-%06d", m.seq)
	it.Modality = strings.ToUpper(it.Modality)
	if it.Status == "" {
		it.Status = "scheduled"
	}
	if !mwlStatuses[it.Status] {
		return WorklistItem{}, fmt.Errorf("bad status %q", it.Status)
	}
	m.items[it.ID] = &it
	return it, nil
}

// Query is what the modality calls: give me my work for today. Filters by
// modality and, optionally, station AE title; scheduled-date prefix filter
// (YYYY-MM-DD) keeps the response small on slow links.
func (m *MWL) Query(modality, stationAE, datePrefix string, activeOnly bool) []WorklistItem {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []WorklistItem{}
	for _, it := range m.items {
		if modality != "" && it.Modality != strings.ToUpper(modality) {
			continue
		}
		if stationAE != "" && it.StationAE != "" && it.StationAE != stationAE {
			continue
		}
		if datePrefix != "" && !strings.HasPrefix(it.Scheduled, datePrefix) {
			continue
		}
		if activeOnly && (it.Status == "completed" || it.Status == "cancelled") {
			continue
		}
		out = append(out, *it)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Scheduled < out[j].Scheduled })
	return out
}

// Advance moves an item through its lifecycle; guards transitions so a
// completed study can never silently revert to scheduled.
func (m *MWL) Advance(id, status, actor string) (WorklistItem, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	it, ok := m.items[id]
	if !ok {
		return WorklistItem{}, errMWLNotFound
	}
	if !mwlStatuses[status] {
		return WorklistItem{}, fmt.Errorf("bad status %q", status)
	}
	allowed := false
	for _, s := range mwlTransitions[it.Status] {
		if s == status {
			allowed = true
		}
	}
	if !allowed {
		return WorklistItem{}, fmt.Errorf("%w: %s → %s", errMWLTransition, it.Status, status)
	}
	it.Status = status
	log.Printf("mwl %s → %s by %s", id, status, actor)
	return *it, nil
}

var errMWLNotFound = errors.New("worklist item not found")
var errMWLTransition = errors.New("illegal worklist transition")

// ------------------------------------------------------------- device ingest

// DeviceReading is one observation batch from a point-of-care device.
// Code is a LOINC/本地 code string — carried opaquely, mapped downstream.
type DeviceReading struct {
	DeviceID   string  `json:"device_id"`            // asset tag or device serial
	PatientID  string  `json:"patient_id"`           // SHR patient id
	Code       string  `json:"code"`                 // e.g. 8867-4 heart rate
	CodeText   string  `json:"code_text,omitempty"`  // human label
	Value      float64 `json:"value"`
	Unit       string  `json:"unit"`                 // UCUM
	Effective  string  `json:"effective,omitempty"`  // RFC3339; default now
	Encounter  string  `json:"encounter,omitempty"`
	Note       string  `json:"note,omitempty"`
}

// Validate guards the ingest boundary: a reading without identity, code or
// unit is noise, not data.
func (dr DeviceReading) Validate() error {
	if dr.DeviceID == "" || dr.PatientID == "" || dr.Code == "" || dr.Unit == "" {
		return errors.New("device_id, patient_id, code and unit are required")
	}
	if dr.Effective != "" {
		if _, err := time.Parse(time.RFC3339, dr.Effective); err != nil {
			return errors.New("effective must be RFC3339")
		}
	}
	return nil
}

// Observation renders the reading as a minimal FHIR R4 Observation.
func (dr DeviceReading) Observation(now time.Time) map[string]any {
	eff := dr.Effective
	if eff == "" {
		eff = now.UTC().Format(time.RFC3339)
	}
	ob := map[string]any{
		"resourceType": "Observation",
		"status":       "final",
		"category": []any{map[string]any{
			"coding": []any{map[string]any{"system": "http://terminology.hl7.org/CodeSystem/observation-category", "code": "vital-signs"}},
		}},
		"code": map[string]any{
			"coding": []any{map[string]any{"system": "http://loinc.org", "code": dr.Code}},
			"text":   dr.CodeText,
		},
		"subject":   map[string]any{"reference": "Patient/" + dr.PatientID},
		"effectiveDateTime": eff,
		"valueQuantity": map[string]any{
			"value":  dr.Value,
			"unit":   dr.Unit,
			"system": "http://unitsofmeasure.org",
		},
		"device": map[string]any{"display": dr.DeviceID},
	}
	if dr.Encounter != "" {
		ob["encounter"] = map[string]any{"reference": "Encounter/" + dr.Encounter}
	}
	if dr.Note != "" {
		ob["note"] = []any{map[string]any{"text": dr.Note}}
	}
	return ob
}

// DeviceIngest accepts readings, keeps the last N locally (device replay /
// debugging), and forwards each as an Observation to the SHR.
type DeviceIngest struct {
	mu       sync.Mutex
	recent   []DeviceReading // ring, newest last, cap 500
	shrURL   string
	client   *http.Client
	forward  int // forwarded ok
	failed   int // shr unreachable — readings kept locally regardless
	now      func() time.Time
}

func NewDeviceIngest(shrURL string, now func() time.Time) *DeviceIngest {
	return &DeviceIngest{
		shrURL: strings.TrimRight(shrURL, "/"),
		client: &http.Client{Timeout: 3 * time.Second},
		now:    now,
	}
}

// Ingest validates, stores, then fail-soft forwards to the SHR.
// Returns the rendered Observation and whether it reached the SHR.
func (d *DeviceIngest) Ingest(dr DeviceReading) (map[string]any, bool, error) {
	if err := dr.Validate(); err != nil {
		return nil, false, err
	}
	d.mu.Lock()
	d.recent = append(d.recent, dr)
	if len(d.recent) > 500 {
		d.recent = d.recent[len(d.recent)-500:]
	}
	d.mu.Unlock()
	ob := dr.Observation(d.now())
	if d.shrURL == "" {
		return ob, false, nil
	}
	body, err := json.Marshal(ob)
	if err != nil {
		return ob, false, nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, d.shrURL+"/fhir/Observation", bytes.NewReader(body))
	if err != nil {
		return ob, false, nil
	}
	req.Header.Set("Content-Type", "application/fhir+json")
	resp, err := d.client.Do(req)
	d.mu.Lock()
	defer d.mu.Unlock()
	if err != nil || resp.StatusCode >= 300 {
		d.failed++
		if err == nil {
			resp.Body.Close()
		}
		log.Printf("device ingest: SHR forward failed for %s — kept locally", dr.DeviceID)
		return ob, false, nil
	}
	resp.Body.Close()
	d.forward++
	return ob, true, nil
}

// Stats exposes forward health for the ops dashboard.
func (d *DeviceIngest) Stats() map[string]any {
	d.mu.Lock()
	defer d.mu.Unlock()
	return map[string]any{"recent": len(d.recent), "forwarded": d.forward, "failed": d.failed}
}
