// Package main — OpenHealth OS registry service.
//
// OpenHIE-aligned registries (ADR-0002): facilities, providers, and the
// offline-resolvable identity scheme for patients not yet NIN-bound.
// Facility records carry the physical-infrastructure profile (power, water,
// equipment) — the data the facility-readiness dashboards are built from.
//
// Storage is an interface: the in-memory store is the default for edge nodes
// and tests; production binds Postgres on the state plane with the same
// semantics. All read surfaces expose both the internal shape and FHIR R4
// resources (Location for facilities, Practitioner for providers).
package main

import (
	"errors"
	"sort"
	"sync"
)

// Facility is the registry record for one health facility.
type Facility struct {
	ID        string   `json:"id"` // registry id (offline-resolvable, see NewID)
	Name      string   `json:"name"`
	State     string   `json:"state"`     // Nigerian state, e.g. "Kaduna"
	LGA       string   `json:"lga"`       // local government area
	Ownership string   `json:"ownership"` // public | private | faith-based
	Level     string   `json:"level"`     // PHC | clinic | hospital
	Services  []string `json:"services"`  // e.g. ["anc","immunisation","dispensing"]
	BHCPF     bool     `json:"bhcpf"`     // supported by the Basic Health Care Provision Fund
	Power     string   `json:"power"`     // grid | solar | generator | hybrid | none
	Water     string   `json:"water"`     // piped | borehole | well | none
	Latitude  float64  `json:"latitude,omitempty"`
	Longitude float64  `json:"longitude,omitempty"`
	// Capacity profile (ADR-0014) — what the map renders and referrals
	// route on: beds, clinical staffing, amenities, contact, hours.
	Beds      int      `json:"beds"`
	Doctors   int      `json:"doctors"`
	Nurses    int      `json:"nurses"`
	Amenities []string `json:"amenities"` // pharmacy | lab | maternity | xray | ambulance | oxygen | cold-chain
	Phone     string   `json:"phone,omitempty"`
	Hours     string   `json:"hours,omitempty"` // e.g. "24/7" | "Mon-Fri 08:00-16:00"
}

// Provider is the registry record for one health worker.
type Provider struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	License  string `json:"license"`  // MDCN / nursing council / CHEW licence number
	Cadre    string `json:"cadre"`    // nurse | CHO | CHEW | physician | midwife
	Facility string `json:"facility"` // primary facility registry id
	Verified bool   `json:"verified"` // registry-verified licence (MDCN adapter seam)
}

// Store is the registry persistence seam.
type Store interface {
	PutFacility(f Facility) error
	GetFacility(id string) (Facility, error)
	ListFacilities() []Facility
	PutProvider(p Provider) error
	GetProvider(id string) (Provider, error)
	ListProviders() []Provider
}

var ErrNotFound = errors.New("not found")
var ErrInvalid = errors.New("invalid record: id and name are required")

// MemoryStore is the default store (edge nodes, tests).
type MemoryStore struct {
	mu         sync.RWMutex
	facilities map[string]Facility
	providers  map[string]Provider
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{facilities: map[string]Facility{}, providers: map[string]Provider{}}
}

func (m *MemoryStore) PutFacility(f Facility) error {
	if f.ID == "" || f.Name == "" {
		return ErrInvalid
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.facilities[f.ID] = f
	return nil
}

func (m *MemoryStore) GetFacility(id string) (Facility, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	f, ok := m.facilities[id]
	if !ok {
		return Facility{}, ErrNotFound
	}
	return f, nil
}

func (m *MemoryStore) ListFacilities() []Facility {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]Facility, 0, len(m.facilities))
	for _, f := range m.facilities {
		out = append(out, f)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

func (m *MemoryStore) PutProvider(p Provider) error {
	if p.ID == "" || p.Name == "" {
		return ErrInvalid
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.providers[p.ID] = p
	return nil
}

func (m *MemoryStore) GetProvider(id string) (Provider, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	p, ok := m.providers[id]
	if !ok {
		return Provider{}, ErrNotFound
	}
	return p, nil
}

func (m *MemoryStore) ListProviders() []Provider {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]Provider, 0, len(m.providers))
	for _, p := range m.providers {
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}
