// Package rebac — relationship-based authorization for OpenHealth OS
// (ADR-0015), the Permify row. Permify itself stays an adoption option;
// the doctrine it implements (Zanzibar-shaped tuples + derived checks)
// is implemented here stdlib-first so the platform never ships a
// dependency to answer "may this nurse see this patient?".
//
// Model: (subject, relation, object) tuples with optional expiry.
//
//	user:dr-adaeze   staff    facility:OH-LAG-0042
//	user:mama-ndidi  guardian patient:OH-LAG-0042-00017
//
// The derived rule the platform actually enforces
// (Checker.CanAccessPatient):
//
//  1. a direct unexpired tuple on the patient (guardian | care-team |
//     referral) grants access — this is how guardians and cross-facility
//     referrals work;
//  2. otherwise, staff | admin of the patient's *registering facility*
//     grants access — the common case: your facility's patients are
//     yours to treat;
//  3. nothing else grants access. Unattributed access is denied.
//
// Stores: in-memory (edge/tests) and Postgres (state plane, migration
// 0009_rebac.sql), behind one Store seam — same discipline as every
// other platform store.
package rebac

import (
	"errors"
	"sort"
	"sync"
	"time"
)

// Entity is a typed id: ("user", "dr-adaeze"), ("facility", "OH-LAG-0042").
type Entity struct {
	Type string `json:"type"`
	ID   string `json:"id"`
}

// Tuple is one grant: Subject has Relation on Object until ExpiresAt
// (nil = standing).
type Tuple struct {
	Subject   Entity     `json:"subject"`
	Relation  string     `json:"relation"`
	Object    Entity     `json:"object"`
	GrantedBy string     `json:"granted_by,omitempty"`
	ExpiresAt *time.Time `json:"expires_at,omitempty"`
}

// ErrInvalid rejects malformed tuples at the store boundary.
var ErrInvalid = errors.New("rebac: invalid tuple")

// Patient-level relations that grant direct access.
var patientAccessRelations = []string{"guardian", "care-team", "referral"}

// Facility-level relations whose holders reach the facility's patients.
var facilityStaffRelations = []string{"staff", "admin"}

// Store is the tuple persistence seam.
type Store interface {
	// Grant upserts a tuple (re-granting refreshes expiry/granted_by).
	Grant(t Tuple) error
	// Revoke removes the exact (subject, relation, object) triple.
	Revoke(subject Entity, relation string, object Entity) error
	// Check reports whether an unexpired tuple exists.
	Check(subject Entity, relation string, object Entity) (bool, error)
	// List returns all unexpired tuples on an object (audit view).
	List(object Entity) ([]Tuple, error)
}

func (t Tuple) valid() bool {
	return t.Subject.Type != "" && t.Subject.ID != "" && t.Relation != "" &&
		t.Object.Type != "" && t.Object.ID != ""
}

func expired(t Tuple, now time.Time) bool {
	return t.ExpiresAt != nil && now.After(*t.ExpiresAt)
}

// ---------- in-memory ----------

// MemoryStore for edge processes and tests.
type MemoryStore struct {
	mu     sync.Mutex
	tuples map[string]Tuple
	now    func() time.Time
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{tuples: map[string]Tuple{}, now: time.Now}
}

func tupleKey(subject Entity, relation string, object Entity) string {
	return subject.Type + ":" + subject.ID + "|" + relation + "|" + object.Type + ":" + object.ID
}

func (m *MemoryStore) Grant(t Tuple) error {
	if !t.valid() {
		return ErrInvalid
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.tuples[tupleKey(t.Subject, t.Relation, t.Object)] = t
	return nil
}

func (m *MemoryStore) Revoke(subject Entity, relation string, object Entity) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	delete(m.tuples, tupleKey(subject, relation, object))
	return nil
}

func (m *MemoryStore) Check(subject Entity, relation string, object Entity) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	t, ok := m.tuples[tupleKey(subject, relation, object)]
	if !ok || expired(t, m.now()) {
		return false, nil
	}
	return true, nil
}

func (m *MemoryStore) List(object Entity) ([]Tuple, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []Tuple
	for _, t := range m.tuples {
		if t.Object == object && !expired(t, m.now()) {
			out = append(out, t)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Subject.Type != out[j].Subject.Type {
			return out[i].Subject.Type < out[j].Subject.Type
		}
		if out[i].Subject.ID != out[j].Subject.ID {
			return out[i].Subject.ID < out[j].Subject.ID
		}
		return out[i].Relation < out[j].Relation
	})
	return out, nil
}

// ---------- checker ----------

// Checker derives access decisions from tuples. It is deliberately small:
// one domain rule (patient access) stated once, so a swap to Permify
// behind this seam changes the engine, never the policy.
type Checker struct {
	Store Store
}

// PatientRef is the patient half of an access decision.
type PatientRef struct {
	ID         string
	FacilityID string // registering facility ("" = unaffiliated record)
}

// CanAccessPatient applies the platform's patient-access rule.
// userID is the authenticated subject (X-User-ID at the edge).
func (c *Checker) CanAccessPatient(userID string, p PatientRef) (bool, error) {
	if userID == "" || p.ID == "" {
		return false, nil
	}
	user := Entity{Type: "user", ID: userID}
	patient := Entity{Type: "patient", ID: p.ID}
	// Rule 1: direct patient-level grants (guardian, care team, referral).
	for _, rel := range patientAccessRelations {
		ok, err := c.Store.Check(user, rel, patient)
		if err != nil {
			return false, err
		}
		if ok {
			return true, nil
		}
	}
	// Rule 2: staff/admin of the registering facility.
	if p.FacilityID != "" {
		fac := Entity{Type: "facility", ID: p.FacilityID}
		for _, rel := range facilityStaffRelations {
			ok, err := c.Store.Check(user, rel, fac)
			if err != nil {
				return false, err
			}
			if ok {
				return true, nil
			}
		}
	}
	return false, nil
}

// IsFacilityAdmin reports whether userID administers the facility —
// the gate for managing grants on its behalf.
func (c *Checker) IsFacilityAdmin(userID, facilityID string) (bool, error) {
	if userID == "" || facilityID == "" {
		return false, nil
	}
	return c.Store.Check(
		Entity{Type: "user", ID: userID}, "admin",
		Entity{Type: "facility", ID: facilityID})
}
