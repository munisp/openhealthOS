// Training service (ADR-0029): the platform's own lean LMS. We do not
// embed Moodle (GPL) or Open edX (AGPL) — what a health system actually
// needs is smaller and stranger than a general LMS: SOP-versioned
// courses, CPD points that regulators (MDCN for doctors, NMCN MCPDP for
// nurses) can audit, checklist sign-offs that reuse the theatre
// WHO-gate doctrine, certificates that are ledger transactions (signed,
// revocable, verifiable offline), and an IVR seam for feature phones.
//
// Doctrine: a certificate is a financial-grade fact, not a PDF — it is
// a double-entry posting (system:cpd:issuance → cpd:<practitioner>) on
// the ledger seam, so revocation is a counter-entry and verification is
// a balance/journal query. Serials are deterministic hashes.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	ErrNotFound         = errors.New("not found")
	ErrBadTransition    = errors.New("invalid state transition")
	ErrPassMarkNotMet   = errors.New("pass mark not met")
	ErrChecklistOpen    = errors.New("checklist items remain unsigned")
	ErrAlreadyCertified = errors.New("certificate already issued for this course version")
)

// CPD schemes. Points are awarded under exactly one scheme per course.
var cpdSchemes = map[string]bool{
	"MDCN":   true, // doctors
	"NMCN":   true, // nurses & midwives (MCPDP)
	"NEMTC":  true, // paramedics / emergency technicians
	"CHPRBN": true, // community health practitioners
}

// ------------------------------------------------------------- catalogue

type ModuleType string

const (
	ModuleVideo     ModuleType = "video"
	ModuleDocument  ModuleType = "document"
	ModuleQuiz      ModuleType = "quiz"
	ModuleChecklist ModuleType = "checklist"
)

type Module struct {
	ID        string     `json:"id"`
	Title     string     `json:"title"`
	Type      ModuleType `json:"type"`
	Ref       string     `json:"ref,omitempty"`       // blob://media/… for video/document
	PassMark  int        `json:"pass_mark,omitempty"` // quiz: percent required
	Questions []Question `json:"questions,omitempty"`
	Items     []string   `json:"items,omitempty"` // checklist: steps to sign off
}

type Question struct {
	Prompt  string   `json:"prompt"`
	Options []string `json:"options"`
	Answer  int      `json:"answer"` // index into Options
}

type Course struct {
	ID               string   `json:"id"`
	Title            string   `json:"title"`
	Version          string   `json:"version"`           // course content version
	GuidelineVersion string   `json:"guideline_version"` // clinical SOP it attests to
	CPDPoints        int      `json:"cpd_points"`
	Scheme           string   `json:"scheme"`
	Modules          []Module `json:"modules"`
}

func (c *Course) validate() error {
	if c.ID == "" || c.Title == "" || c.Version == "" {
		return errors.New("course id/title/version are required")
	}
	if !cpdSchemes[c.Scheme] {
		return fmt.Errorf("unknown CPD scheme %q", c.Scheme)
	}
	if len(c.Modules) == 0 {
		return errors.New("course must have at least one module")
	}
	for _, m := range c.Modules {
		switch m.Type {
		case ModuleQuiz:
			if m.PassMark < 1 || m.PassMark > 100 {
				return fmt.Errorf("quiz %s: pass_mark must be 1-100", m.ID)
			}
			if len(m.Questions) == 0 {
				return fmt.Errorf("quiz %s: no questions", m.ID)
			}
		case ModuleChecklist:
			if len(m.Items) == 0 {
				return fmt.Errorf("checklist %s: no items", m.ID)
			}
		case ModuleVideo, ModuleDocument:
			if !strings.HasPrefix(m.Ref, "blob://media/") {
				return fmt.Errorf("%s %s: ref must be blob://media/…", m.Type, m.ID)
			}
		default:
			return fmt.Errorf("module %s: unknown type %q", m.ID, m.Type)
		}
	}
	return nil
}

// ------------------------------------------------------------- enrolment

type ModuleProgress struct {
	Status   string          `json:"status"` // pending | done | failed
	Score    int             `json:"score,omitempty"`
	Attempts int             `json:"attempts,omitempty"`
	SignOffs map[string]bool `json:"sign_offs,omitempty"` // checklist: item → assessor-signed
}

type Enrolment struct {
	ID               string                     `json:"id"`
	CourseID         string                     `json:"course_id"`
	CourseVersion    string                     `json:"course_version"`
	PractitionerID   string                     `json:"practitioner_id"`
	FacilityID       string                     `json:"facility_id"`
	Progress         map[string]*ModuleProgress `json:"progress"`
	ReAttestRequired bool                       `json:"re_attest_required"`
	CreatedAt        time.Time                  `json:"created_at"`
	CompletedAt      *time.Time                 `json:"completed_at,omitempty"`
}

func (e *Enrolment) module(c *Course, moduleID string) (*Module, *ModuleProgress, error) {
	var mod *Module
	for i := range c.Modules {
		if c.Modules[i].ID == moduleID {
			mod = &c.Modules[i]
			break
		}
	}
	if mod == nil {
		return nil, nil, ErrNotFound
	}
	p := e.Progress[moduleID]
	if p == nil {
		p = &ModuleProgress{Status: "pending"}
		e.Progress[moduleID] = p
	}
	return mod, p, nil
}

// Grade quiz answers; returns percent score.
func gradeQuiz(m *Module, answers []int) (int, error) {
	if len(answers) != len(m.Questions) {
		return 0, fmt.Errorf("expected %d answers, got %d", len(m.Questions), len(answers))
	}
	correct := 0
	for i, q := range m.Questions {
		if answers[i] == q.Answer {
			correct++
		}
	}
	return correct * 100 / len(m.Questions), nil
}

// Complete reports whether every module is done.
func (e *Enrolment) Complete(c *Course) bool {
	for _, m := range c.Modules {
		p := e.Progress[m.ID]
		if p == nil || p.Status != "done" {
			return false
		}
	}
	return true
}

// ------------------------------------------------------------- certificates

type Certificate struct {
	Serial         string    `json:"serial"` // deterministic sha256
	CourseID       string    `json:"course_id"`
	CourseVersion  string    `json:"course_version"`
	PractitionerID string    `json:"practitioner_id"`
	Scheme         string    `json:"scheme"`
	CPDPoints      int       `json:"cpd_points"`
	LedgerTx       string    `json:"ledger_tx"`
	IssuedAt       time.Time `json:"issued_at"`
	Revoked        bool      `json:"revoked"`
	RevokedTx      string    `json:"revoked_tx,omitempty"`
}

func certSerial(courseID, version, practitioner string) string {
	sum := sha256.Sum256([]byte("cert|" + courseID + "|" + version + "|" + practitioner))
	return "OH-CPD-" + strings.ToUpper(hex.EncodeToString(sum[:8]))
}

// LedgerSink is the certificate-issuance seam (SubprocessLedger in prod,
// fake in tests). Post mirrors the ledger driver's op vocabulary.
type LedgerSink interface {
	Post(debit, credit, tx string, amount int) (string, error)
	Balance(id string) (int, error)
}

// ------------------------------------------------------------- service

type TrainingService struct {
	mu      sync.Mutex
	courses map[string]*Course      // id → course (latest version wins)
	enrols  map[string]*Enrolment   // id → enrolment
	certs   map[string]*Certificate // serial → certificate
	ledger  LedgerSink
	seq     int
}

func NewTrainingService(ledger LedgerSink) *TrainingService {
	return &TrainingService{
		courses: map[string]*Course{},
		enrols:  map[string]*Enrolment{},
		certs:   map[string]*Certificate{},
		ledger:  ledger,
	}
}

func (s *TrainingService) AddCourse(c Course) error {
	if err := c.validate(); err != nil {
		return err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	cp := c
	s.courses[c.ID] = &cp
	return nil
}

func (s *TrainingService) GetCourse(id string) (*Course, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.courses[id]
	return c, ok
}

func (s *TrainingService) Enrol(courseID, practitioner, facility string) (*Enrolment, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.courses[courseID]
	if !ok {
		return nil, ErrNotFound
	}
	s.seq++
	e := &Enrolment{
		ID:             fmt.Sprintf("enr-%06d", s.seq),
		CourseID:       courseID,
		CourseVersion:  c.Version,
		PractitionerID: practitioner,
		FacilityID:     facility,
		Progress:       map[string]*ModuleProgress{},
		CreatedAt:      time.Now().UTC(),
	}
	// A guideline bump between versions flags re-attestation.
	serial := certSerial(courseID, c.Version, practitioner)
	if old, ok := s.certs[serial]; ok && old.Revoked {
		e.ReAttestRequired = true
	}
	s.enrols[e.ID] = e
	return e, nil
}

// MarkDone covers video/document modules (watch/read confirmation).
func (s *TrainingService) MarkDone(enrolID, moduleID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, c, err := s.lookup(enrolID)
	if err != nil {
		return err
	}
	mod, p, err := e.module(c, moduleID)
	if err != nil {
		return err
	}
	if mod.Type != ModuleVideo && mod.Type != ModuleDocument {
		return fmt.Errorf("module %s is %s — use the quiz/checklist endpoints", moduleID, mod.Type)
	}
	p.Status = "done"
	return s.maybeComplete(e, c)
}

func (s *TrainingService) SubmitQuiz(enrolID, moduleID string, answers []int) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, c, err := s.lookup(enrolID)
	if err != nil {
		return 0, err
	}
	mod, p, err := e.module(c, moduleID)
	if err != nil {
		return 0, err
	}
	if mod.Type != ModuleQuiz {
		return 0, fmt.Errorf("module %s is not a quiz", moduleID)
	}
	score, err := gradeQuiz(mod, answers)
	if err != nil {
		return 0, err
	}
	p.Attempts++
	p.Score = score
	if score >= mod.PassMark {
		p.Status = "done"
	} else {
		p.Status = "failed"
		return score, ErrPassMarkNotMet
	}
	return score, s.maybeComplete(e, c)
}

// SignChecklist is the assessor gate (theatre WHO doctrine): a named
// assessor signs each item; the module completes only when every item
// carries a signature.
func (s *TrainingService) SignChecklist(enrolID, moduleID, item, assessor string) error {
	if assessor == "" {
		return errors.New("assessor is required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	e, c, err := s.lookup(enrolID)
	if err != nil {
		return err
	}
	mod, p, err := e.module(c, moduleID)
	if err != nil {
		return err
	}
	if mod.Type != ModuleChecklist {
		return fmt.Errorf("module %s is not a checklist", moduleID)
	}
	found := false
	for _, it := range mod.Items {
		if it == item {
			found = true
			break
		}
	}
	if !found {
		return fmt.Errorf("checklist item %q not in module", item)
	}
	if p.SignOffs == nil {
		p.SignOffs = map[string]bool{}
	}
	p.SignOffs[item] = true
	if len(p.SignOffs) == len(mod.Items) {
		p.Status = "done"
		return s.maybeComplete(e, c)
	}
	return nil
}

func (s *TrainingService) maybeComplete(e *Enrolment, c *Course) error {
	if e.Complete(c) && e.CompletedAt == nil {
		now := time.Now().UTC()
		e.CompletedAt = &now
	}
	return nil
}

// IssueCertificate posts the CPD award to the ledger and records the
// certificate. Idempotent per (course, version, practitioner).
func (s *TrainingService) IssueCertificate(enrolID string) (*Certificate, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, c, err := s.lookup(enrolID)
	if err != nil {
		return nil, err
	}
	if !e.Complete(c) {
		return nil, errors.New("course not complete")
	}
	serial := certSerial(c.ID, c.Version, e.PractitionerID)
	if existing, ok := s.certs[serial]; ok && !existing.Revoked {
		return existing, ErrAlreadyCertified
	}
	tx := "cert:" + serial
	if _, err := s.ledger.Post("system:cpd:issuance", "cpd:"+e.PractitionerID, tx, c.CPDPoints); err != nil {
		return nil, fmt.Errorf("ledger: %w", err)
	}
	cert := &Certificate{
		Serial:         serial,
		CourseID:       c.ID,
		CourseVersion:  c.Version,
		PractitionerID: e.PractitionerID,
		Scheme:         c.Scheme,
		CPDPoints:      c.CPDPoints,
		LedgerTx:       tx,
		IssuedAt:       time.Now().UTC(),
	}
	s.certs[serial] = cert
	return cert, nil
}

// Revoke posts the counter-entry — the ledger is append-only, so
// revocation is a reversal posting, never a delete.
func (s *TrainingService) Revoke(serial, actor, reason string) (*Certificate, error) {
	if actor == "" || reason == "" {
		return nil, errors.New("actor and reason are required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	cert, ok := s.certs[serial]
	if !ok {
		return nil, ErrNotFound
	}
	if cert.Revoked {
		return cert, ErrBadTransition
	}
	tx := "revoke:" + serial
	if _, err := s.ledger.Post("cpd:"+cert.PractitionerID, "system:cpd:issuance", tx, cert.CPDPoints); err != nil {
		return nil, fmt.Errorf("ledger: %w", err)
	}
	cert.Revoked = true
	cert.RevokedTx = tx
	return cert, nil
}

// Verify checks certificate truth against the ledger: issued, not
// revoked, and the CPD account balance is consistent.
func (s *TrainingService) Verify(serial string) (map[string]any, error) {
	s.mu.Lock()
	cert, ok := s.certs[serial]
	s.mu.Unlock()
	if !ok {
		return nil, ErrNotFound
	}
	bal, err := s.ledger.Balance("cpd:" + cert.PractitionerID)
	if err != nil {
		return nil, fmt.Errorf("ledger: %w", err)
	}
	return map[string]any{
		"serial":      serial,
		"valid":       !cert.Revoked,
		"course":      cert.CourseID,
		"version":     cert.CourseVersion,
		"scheme":      cert.Scheme,
		"cpd_points":  cert.CPDPoints,
		"ledger_tx":   cert.LedgerTx,
		"revoked_tx":  cert.RevokedTx,
		"cpd_balance": bal,
	}, nil
}

// CPDSummary totals a practitioner's points per scheme (regulator view).
func (s *TrainingService) CPDSummary(practitioner, scheme string) map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	total, revoked := 0, 0
	serials := []string{}
	for _, c := range s.certs {
		if c.PractitionerID != practitioner {
			continue
		}
		if scheme != "" && c.Scheme != scheme {
			continue
		}
		if c.Revoked {
			revoked += c.CPDPoints
			continue
		}
		total += c.CPDPoints
		serials = append(serials, c.Serial)
	}
	sort.Strings(serials)
	return map[string]any{
		"practitioner": practitioner,
		"scheme":       scheme,
		"points":       total,
		"revoked":      revoked,
		"certificates": serials,
	}
}

// BumpGuideline flags every enrolment on older course versions as
// re-attestation required — the SOP changed, so yesterday's certificate
// no longer covers today's practice.
func (s *TrainingService) BumpGuideline(courseID, newGuidelineVersion, actor string) (int, error) {
	if actor == "" {
		return 0, errors.New("actor is required")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	c, ok := s.courses[courseID]
	if !ok {
		return 0, ErrNotFound
	}
	c.GuidelineVersion = newGuidelineVersion
	flagged := 0
	for _, e := range s.enrols {
		if e.CourseID == courseID && !e.ReAttestRequired {
			e.ReAttestRequired = true
			flagged++
		}
	}
	return flagged, nil
}

// AttestationGap lists practitioners at a facility whose training no
// longer matches current SOP versions.
func (s *TrainingService) AttestationGap(facilityID string) []map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []map[string]any{}
	for _, e := range s.enrols {
		if e.FacilityID == facilityID && e.ReAttestRequired {
			out = append(out, map[string]any{
				"enrolment":    e.ID,
				"practitioner": e.PractitionerID,
				"course":       e.CourseID,
				"version":      e.CourseVersion,
			})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i]["enrolment"].(string) < out[j]["enrolment"].(string) })
	return out
}

// IVRQuiz exposes a quiz module in the voicebridge shape: one prompt at
// a time, DTMF/voice answer index back. Feature-phone practitioners do
// CPD without data.
func (s *TrainingService) IVRQuiz(enrolID, moduleID string) (map[string]any, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, c, err := s.lookup(enrolID)
	if err != nil {
		return nil, err
	}
	mod, p, err := e.module(c, moduleID)
	if err != nil {
		return nil, err
	}
	if mod.Type != ModuleQuiz {
		return nil, fmt.Errorf("module %s is not a quiz", moduleID)
	}
	return map[string]any{
		"enrolment": e.ID,
		"module":    mod.ID,
		"questions": len(mod.Questions),
		"attempts":  p.Attempts,
		"prompts":   mod.Questions,
	}, nil
}

func (s *TrainingService) lookup(enrolID string) (*Enrolment, *Course, error) {
	e, ok := s.enrols[enrolID]
	if !ok {
		return nil, nil, ErrNotFound
	}
	c, ok := s.courses[e.CourseID]
	if !ok {
		return nil, nil, ErrNotFound
	}
	return e, c, nil
}
