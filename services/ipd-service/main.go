// Package main — OpenHealth OS inpatient department (IPD) service
// (ADR-0017): wards, beds and ADT (admit–transfer–discharge).
//
// Why this exists: the platform was deliberately outpatient-first —
// PHC, telehealth, claims. A general hospital cannot run on that:
// the bed board (which ward, which bed, who is in it) and the ADT
// stream (admission → transfer → discharge, with the discharge
// summary flowing back into the shared record) are the minimum a
// hospital calls "management". This service closes that gap without
// touching the outpatient spine.
//
// Invariants (all enforced inside one store lock/transaction):
//   - a bed holds at most one admitted patient;
//   - a patient has at most one open admission;
//   - transfer is atomic: the destination bed is taken before the
//     source bed is released — a failed transfer never strands a
//     patient between beds;
//   - discharge frees the bed into cleaning (never straight to free —
//     infection control is a state, not a hope) and writes the
//     discharge summary back to the SHR as an inpatient Encounter +
//     DocumentReference, so the patient's phone shows the stay.
package main

import (
	"errors"
	"sort"
	"sync"
	"time"
)

var (
	ErrNotFound        = errors.New("not found")
	ErrBedOccupied     = errors.New("bed is not free")
	ErrAlreadyAdmitted = errors.New("patient already has an open admission")
	ErrNotAdmitted     = errors.New("admission is not open")
	ErrBadWard         = errors.New("invalid ward: id, facility_id and name are required")
)

// Bed states: free → occupied → cleaning → free.
const (
	BedFree     = "free"
	BedOccupied = "occupied"
	BedCleaning = "cleaning"
)

const (
	AdmOpen       = "admitted"
	AdmDischarged = "discharged"
)

type Ward struct {
	ID         string `json:"id"`
	FacilityID string `json:"facility_id"`
	Name       string `json:"name"`
	Kind       string `json:"kind"` // medical | surgical | maternity | paediatric | isolation
}

type Bed struct {
	ID     string `json:"id"`
	WardID string `json:"ward_id"`
	Label  string `json:"label"` // human label — "M3"
	State  string `json:"state"`
}

// Move is one bed-to-bed transfer inside an admission.
type Move struct {
	FromBedID string `json:"from_bed_id"`
	ToBedID   string `json:"to_bed_id"`
	At        string `json:"at"`
}

type Admission struct {
	ID           string `json:"id"`
	PatientID    string `json:"patient_id"`
	FacilityID   string `json:"facility_id"`
	WardID       string `json:"ward_id"`
	BedID        string `json:"bed_id"`
	Reason       string `json:"reason"`
	Status       string `json:"status"` // admitted | discharged
	AdmittedAt   string `json:"admitted_at"`
	DischargedAt string `json:"discharged_at,omitempty"`
	Summary      string `json:"summary,omitempty"` // discharge summary text
	Moves        []Move `json:"moves,omitempty"`
}

// Store is the persistence seam (memory edge / Postgres state).
type Store interface {
	PutWard(w Ward) error
	Ward(id string) (Ward, error)
	Wards(facilityID string) []Ward
	PutBed(b Bed) error
	Bed(id string) (Bed, error)
	Beds(wardID string) []Bed
	// Admit atomically: bed must be free, patient must not be open.
	Admit(a Admission) error
	Admission(id string) (Admission, error)
	OpenAdmission(patientID string) (Admission, error)
	Admissions(facilityID, status string) []Admission
	// Transfer atomically moves an open admission to a free bed.
	Transfer(id, toBedID, at string) (Admission, error)
	// Discharge closes the admission and sends the bed to cleaning.
	Discharge(id, at, summary string) (Admission, error)
	// SetBedState is the housekeeping rail (cleaning → free).
	SetBedState(id, state string) error
}

// ---------------- memory store ----------------

type MemoryStore struct {
	mu    sync.Mutex
	wards map[string]Ward
	beds  map[string]Bed
	adms  map[string]Admission
	seq   int
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{wards: map[string]Ward{}, beds: map[string]Bed{}, adms: map[string]Admission{}}
}

func (m *MemoryStore) PutWard(w Ward) error {
	if w.ID == "" || w.FacilityID == "" || w.Name == "" {
		return ErrBadWard
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.wards[w.ID] = w
	return nil
}

func (m *MemoryStore) Ward(id string) (Ward, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if w, ok := m.wards[id]; ok {
		return w, nil
	}
	return Ward{}, ErrNotFound
}

func (m *MemoryStore) Wards(facilityID string) []Ward {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Ward{}
	for _, w := range m.wards {
		if facilityID == "" || w.FacilityID == facilityID {
			out = append(out, w)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (m *MemoryStore) PutBed(b Bed) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.wards[b.WardID]; !ok {
		return ErrNotFound
	}
	if b.State == "" {
		b.State = BedFree
	}
	m.beds[b.ID] = b
	return nil
}

func (m *MemoryStore) Bed(id string) (Bed, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if b, ok := m.beds[id]; ok {
		return b, nil
	}
	return Bed{}, ErrNotFound
}

func (m *MemoryStore) Beds(wardID string) []Bed {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Bed{}
	for _, b := range m.beds {
		if b.WardID == wardID {
			out = append(out, b)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (m *MemoryStore) Admit(a Admission) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	b, ok := m.beds[a.BedID]
	if !ok {
		return ErrNotFound
	}
	if b.State != BedFree {
		return ErrBedOccupied
	}
	if _, ok := m.wards[a.WardID]; !ok {
		return ErrNotFound
	}
	for _, x := range m.adms {
		if x.PatientID == a.PatientID && x.Status == AdmOpen {
			return ErrAlreadyAdmitted
		}
	}
	b.State = BedOccupied
	m.beds[b.ID] = b
	if a.Status == "" {
		a.Status = AdmOpen
	}
	m.adms[a.ID] = a
	return nil
}

func (m *MemoryStore) Admission(id string) (Admission, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if a, ok := m.adms[id]; ok {
		return a, nil
	}
	return Admission{}, ErrNotFound
}

func (m *MemoryStore) OpenAdmission(patientID string) (Admission, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, a := range m.adms {
		if a.PatientID == patientID && a.Status == AdmOpen {
			return a, nil
		}
	}
	return Admission{}, ErrNotFound
}

func (m *MemoryStore) Admissions(facilityID, status string) []Admission {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Admission{}
	for _, a := range m.adms {
		if (facilityID == "" || a.FacilityID == facilityID) && (status == "" || a.Status == status) {
			out = append(out, a)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].AdmittedAt > out[j].AdmittedAt })
	return out
}

func (m *MemoryStore) Transfer(id, toBedID, at string) (Admission, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.adms[id]
	if !ok {
		return Admission{}, ErrNotFound
	}
	if a.Status != AdmOpen {
		return Admission{}, ErrNotAdmitted
	}
	to, ok := m.beds[toBedID]
	if !ok {
		return Admission{}, ErrNotFound
	}
	if to.State != BedFree {
		return Admission{}, ErrBedOccupied
	}
	// Take the destination before releasing the source — a failed
	// transfer never strands the patient.
	to.State = BedOccupied
	m.beds[to.ID] = to
	from := m.beds[a.BedID]
	from.State = BedCleaning
	m.beds[from.ID] = from
	a.Moves = append(a.Moves, Move{FromBedID: a.BedID, ToBedID: toBedID, At: at})
	a.BedID = toBedID
	m.adms[id] = a
	return a, nil
}

func (m *MemoryStore) Discharge(id, at, summary string) (Admission, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.adms[id]
	if !ok {
		return Admission{}, ErrNotFound
	}
	if a.Status != AdmOpen {
		return Admission{}, ErrNotAdmitted
	}
	a.Status = AdmDischarged
	a.DischargedAt = at
	a.Summary = summary
	m.adms[id] = a
	bed := m.beds[a.BedID]
	bed.State = BedCleaning
	m.beds[bed.ID] = bed
	return a, nil
}

func (m *MemoryStore) SetBedState(id, state string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	b, ok := m.beds[id]
	if !ok {
		return ErrNotFound
	}
	b.State = state
	m.beds[id] = b
	return nil
}

// NextID issues durable sequential admission ids (ADM-NNNN).
func (m *MemoryStore) NextID() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	return "ADM-" + itoa4(m.seq)
}

func itoa4(n int) string {
	s := []byte("0000")
	for i := 3; i >= 0 && n > 0; i-- {
		s[i] = byte('0' + n%10)
		n /= 10
	}
	for n > 0 { // beyond 9999 — grow honestly
		s = append([]byte{byte('0' + n%10)}, s...)
		n /= 10
	}
	return string(s)
}

var _ = time.Now // time is the caller's; stores never invent timestamps
