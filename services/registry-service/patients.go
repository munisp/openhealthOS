package main

// Patient identity (ADR-0002): NIN-bound where available, with a
// locally-resolvable offline ID for the unlinked — progressive enrolment.
// Offline IDs are issued by the facility edge and reconcile at the state
// plane on sync; the format encodes the issuing facility so collisions
// across the federation are impossible by construction.

import (
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"
)

// Patient is the registry record for one enrolled person.
type Patient struct {
	ID         string `json:"id"`            // OH-<FACILITY>-<seq> offline id, or NIN-bound id
	NIN        string `json:"nin,omitempty"` // NIMC National Identity Number, when linked
	Name       string `json:"name"`
	DOB        string `json:"dob"`         // YYYY-MM-DD
	Phone      string `json:"phone"`       // USSD/SMS channel for eligibility + reminders
	FacilityID string `json:"facility_id"` // registering facility
	// KYC (ADR-0012): assurance ladder, never blocks care.
	KYCLevel     string `json:"kyc_level"`            // self | phone | nin-format | nin-verified
	KYCDetail    string `json:"kyc_detail,omitempty"` // last verification outcome, human-readable
	RegisteredAt string `json:"registered_at,omitempty"`
}

// PatientStore is the patient persistence seam.
type PatientStore interface {
	// Register issues an offline-resolvable id and stores the record.
	// Registration refuses deterministic duplicates (same NIN, same
	// phone+DOB, same normalised name+DOB) — ErrDuplicate.
	Register(p Patient) (Patient, error)
	// LinkNIN binds an existing offline record to a NIN (progressive enrolment).
	LinkNIN(id, nin string) (Patient, error)
	GetPatient(id string) (Patient, error)
	FindByNIN(nin string) (Patient, error)
	ListPatients() []Patient
	// SearchPatients (ADR-0015): AND-prefix full-text over
	// name/phone/nin/id, ranked. Empty query → nil.
	SearchPatients(q string, limit int) []Patient
	// FindDuplicate returns the existing record p would duplicate,
	// or ErrNotFound when p is unique on every deterministic rule.
	FindDuplicate(p Patient) (Patient, error)
	// SetKYC raises the assurance level (never silently downgrades).
	SetKYC(id, level, detail string) (Patient, error)
	// SaveOTP stores a fresh phone-binding challenge (hash + expiry,
	// resets attempt counter). CheckOTP validates a presented code.
	SaveOTP(id, codeHash string, expires time.Time) error
	CheckOTP(id, codeHash string, now time.Time) error
}

var ErrNINBound = errors.New("record already bound to a NIN")

// otpRecord is one outstanding phone-binding challenge.
type otpRecord struct {
	hash     string
	expires  time.Time
	attempts int
}

const otpMaxAttempts = 5

// MemoryPatientStore is the default patient store (edge nodes, tests).
type MemoryPatientStore struct {
	mu   sync.Mutex
	byID map[string]Patient
	seq  map[string]int // per-facility sequence for offline ids
	otp  map[string]otpRecord
}

func NewMemoryPatientStore() *MemoryPatientStore {
	return &MemoryPatientStore{byID: map[string]Patient{}, seq: map[string]int{}, otp: map[string]otpRecord{}}
}

// OfflineID issues a locally-resolvable id: OH-<facility>-<zero-padded seq>.
// Unique per facility by construction; no coordination needed — works with
// zero connectivity, which is the whole point.
func OfflineID(facilityID string, seq int) string {
	return fmt.Sprintf("OH-%s-%06d", facilityID, seq)
}

func (m *MemoryPatientStore) Register(p Patient) (Patient, error) {
	if p.Name == "" || p.FacilityID == "" {
		return Patient{}, ErrInvalid
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if existing, err := m.findDuplicateLocked(p); err == nil {
		return Patient{}, fmt.Errorf("%w: %s", ErrDuplicate, DuplicateReason(existing))
	}
	if p.ID == "" {
		m.seq[p.FacilityID]++
		p.ID = OfflineID(p.FacilityID, m.seq[p.FacilityID])
	} else if _, taken := m.byID[p.ID]; taken {
		return Patient{}, errors.New("patient id exists: " + p.ID)
	}
	// KYC defaults: a fresh record is self-asserted; a syntactically valid
	// NIN at registration lifts it to nin-format (offline check only).
	p.KYCLevel = KYCSelf
	if p.NIN != "" && ValidNINFormat(p.NIN) {
		p.KYCLevel = KYCNINFormat
	}
	if p.RegisteredAt == "" {
		p.RegisteredAt = time.Now().UTC().Format(time.RFC3339)
	}
	m.byID[p.ID] = p
	return p, nil
}

// findDuplicateLocked applies the deterministic dedupe rules (kyc.go):
// same NIN, same phone+DOB, or same normalised name+DOB.
func (m *MemoryPatientStore) findDuplicateLocked(p Patient) (Patient, error) {
	norm := NormalizeName(p.Name)
	for _, e := range m.byID {
		if p.NIN != "" && e.NIN == p.NIN {
			return e, nil
		}
		if p.Phone != "" && p.DOB != "" && e.Phone == p.Phone && e.DOB == p.DOB {
			return e, nil
		}
		if p.DOB != "" && norm != "" && NormalizeName(e.Name) == norm && e.DOB == p.DOB {
			return e, nil
		}
	}
	return Patient{}, ErrNotFound
}

func (m *MemoryPatientStore) FindDuplicate(p Patient) (Patient, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.findDuplicateLocked(p)
}

func (m *MemoryPatientStore) SetKYC(id, level, detail string) (Patient, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	p, ok := m.byID[id]
	if !ok {
		return Patient{}, ErrNotFound
	}
	if kycRank(level) < kycRank(p.KYCLevel) {
		return p, nil // never downgrade
	}
	p.KYCLevel, p.KYCDetail = level, detail
	m.byID[id] = p
	return p, nil
}

func (m *MemoryPatientStore) SaveOTP(id, codeHash string, expires time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.byID[id]; !ok {
		return ErrNotFound
	}
	m.otp[id] = otpRecord{hash: codeHash, expires: expires}
	return nil
}

func (m *MemoryPatientStore) CheckOTP(id, codeHash string, now time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	rec, ok := m.otp[id]
	if !ok {
		return ErrNotFound
	}
	if rec.attempts >= otpMaxAttempts {
		return ErrOTPLocked
	}
	if now.After(rec.expires) {
		return ErrOTPExpired
	}
	rec.attempts++
	m.otp[id] = rec
	if rec.hash != codeHash {
		return ErrOTPMismatch
	}
	delete(m.otp, id) // one-time use
	return nil
}

func (m *MemoryPatientStore) LinkNIN(id, nin string) (Patient, error) {
	if nin == "" {
		return Patient{}, ErrInvalid
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	p, ok := m.byID[id]
	if !ok {
		return Patient{}, ErrNotFound
	}
	if p.NIN != "" {
		return Patient{}, ErrNINBound
	}
	for _, existing := range m.byID {
		if existing.NIN == nin {
			return Patient{}, errors.New("NIN already bound to " + existing.ID)
		}
	}
	p.NIN = nin
	// A bound NIN is at least format-checked; authority verification is a
	// separate step (POST /patients/{id}/verify).
	if ValidNINFormat(nin) && kycRank(p.KYCLevel) < kycRank(KYCNINFormat) {
		p.KYCLevel = KYCNINFormat
	}
	m.byID[id] = p
	return p, nil
}

func (m *MemoryPatientStore) GetPatient(id string) (Patient, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	p, ok := m.byID[id]
	if !ok {
		return Patient{}, ErrNotFound
	}
	return p, nil
}

func (m *MemoryPatientStore) FindByNIN(nin string) (Patient, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, p := range m.byID {
		if p.NIN == nin {
			return p, nil
		}
	}
	return Patient{}, ErrNotFound
}

func (m *MemoryPatientStore) ListPatients() []Patient {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Patient, 0, len(m.byID))
	for _, p := range m.byID {
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// FHIRPatient projects a patient to the openhealth-patient FHIR R4 profile
// (fhir/profiles/openhealth-patient.json).
func FHIRPatient(p Patient) map[string]any {
	identifiers := []map[string]any{}
	if p.NIN != "" {
		identifiers = append(identifiers, map[string]any{
			"system": "https://nimc.gov.ng/fhir/NamingSystem/nin", "value": p.NIN,
		})
	}
	identifiers = append(identifiers, map[string]any{
		"system": "https://openhealthos.org/fhir/NamingSystem/offline-id", "value": p.ID,
	})
	res := map[string]any{
		"resourceType": "Patient",
		"id":           p.ID,
		"identifier":   identifiers,
		"name":         []map[string]any{{"text": p.Name}},
		"birthDate":    p.DOB,
	}
	if p.Phone != "" {
		res["telecom"] = []map[string]any{{"system": "phone", "value": p.Phone}}
	}
	return res
}
