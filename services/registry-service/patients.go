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
)

// Patient is the registry record for one enrolled person.
type Patient struct {
	ID         string `json:"id"`            // OH-<FACILITY>-<seq> offline id, or NIN-bound id
	NIN        string `json:"nin,omitempty"` // NIMC National Identity Number, when linked
	Name       string `json:"name"`
	DOB        string `json:"dob"`         // YYYY-MM-DD
	Phone      string `json:"phone"`       // USSD/SMS channel for eligibility + reminders
	FacilityID string `json:"facility_id"` // registering facility
}

// PatientStore is the patient persistence seam.
type PatientStore interface {
	// Register issues an offline-resolvable id and stores the record.
	Register(p Patient) (Patient, error)
	// LinkNIN binds an existing offline record to a NIN (progressive enrolment).
	LinkNIN(id, nin string) (Patient, error)
	GetPatient(id string) (Patient, error)
	FindByNIN(nin string) (Patient, error)
	ListPatients() []Patient
}

var ErrNINBound = errors.New("record already bound to a NIN")

// MemoryPatientStore is the default patient store (edge nodes, tests).
type MemoryPatientStore struct {
	mu   sync.Mutex
	byID map[string]Patient
	seq  map[string]int // per-facility sequence for offline ids
}

func NewMemoryPatientStore() *MemoryPatientStore {
	return &MemoryPatientStore{byID: map[string]Patient{}, seq: map[string]int{}}
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
	if p.NIN != "" {
		for _, existing := range m.byID {
			if existing.NIN == p.NIN {
				return Patient{}, errors.New("NIN already registered: " + existing.ID)
			}
		}
	}
	if p.ID == "" {
		m.seq[p.FacilityID]++
		p.ID = OfflineID(p.FacilityID, m.seq[p.FacilityID])
	} else if _, taken := m.byID[p.ID]; taken {
		return Patient{}, errors.New("patient id exists: " + p.ID)
	}
	m.byID[p.ID] = p
	return p, nil
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
