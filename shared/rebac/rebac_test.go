package rebac

import (
	"testing"
	"time"
)

func user(id string) Entity     { return Entity{Type: "user", ID: id} }
func facility(id string) Entity { return Entity{Type: "facility", ID: id} }
func patient(id string) Entity  { return Entity{Type: "patient", ID: id} }

func TestGrantCheckRevoke(t *testing.T) {
	s := NewMemoryStore()
	if err := s.Grant(Tuple{Subject: user("ada"), Relation: "staff", Object: facility("F1")}); err != nil {
		t.Fatal(err)
	}
	ok, _ := s.Check(user("ada"), "staff", facility("F1"))
	if !ok {
		t.Fatal("grant not visible")
	}
	ok, _ = s.Check(user("ada"), "admin", facility("F1"))
	if ok {
		t.Fatal("wrong relation matched")
	}
	ok, _ = s.Check(user("ada"), "staff", facility("F2"))
	if ok {
		t.Fatal("wrong facility matched")
	}
	if err := s.Revoke(user("ada"), "staff", facility("F1")); err != nil {
		t.Fatal(err)
	}
	ok, _ = s.Check(user("ada"), "staff", facility("F1"))
	if ok {
		t.Fatal("revoke ineffective")
	}
}

func TestExpiredGrantsInvisible(t *testing.T) {
	s := NewMemoryStore()
	past := time.Now().Add(-time.Hour)
	future := time.Now().Add(time.Hour)
	s.Grant(Tuple{Subject: user("ada"), Relation: "staff", Object: facility("F1"), ExpiresAt: &past})
	s.Grant(Tuple{Subject: user("ada"), Relation: "admin", Object: facility("F2"), ExpiresAt: &future})
	ok, _ := s.Check(user("ada"), "staff", facility("F1"))
	if ok {
		t.Fatal("expired grant matched")
	}
	ok, _ = s.Check(user("ada"), "admin", facility("F2"))
	if !ok {
		t.Fatal("live grant not matched")
	}
	list, _ := s.List(facility("F1"))
	if len(list) != 0 {
		t.Fatalf("expired tuple in list: %+v", list)
	}
	list, _ = s.List(facility("F2"))
	if len(list) != 1 || list[0].Relation != "admin" {
		t.Fatalf("list: %+v", list)
	}
}

func TestRegrantRefreshesExpiry(t *testing.T) {
	s := NewMemoryStore()
	past := time.Now().Add(-time.Hour)
	s.Grant(Tuple{Subject: user("ada"), Relation: "staff", Object: facility("F1"), ExpiresAt: &past})
	ok, _ := s.Check(user("ada"), "staff", facility("F1"))
	if ok {
		t.Fatal("expired")
	}
	if err := s.Grant(Tuple{Subject: user("ada"), Relation: "staff", Object: facility("F1")}); err != nil {
		t.Fatal(err)
	}
	ok, _ = s.Check(user("ada"), "staff", facility("F1"))
	if !ok {
		t.Fatal("re-grant did not refresh")
	}
}

func TestInvalidTuplesRejected(t *testing.T) {
	s := NewMemoryStore()
	for _, tup := range []Tuple{
		{},
		{Subject: user(""), Relation: "staff", Object: facility("F1")},
		{Subject: user("ada"), Relation: "", Object: facility("F1")},
		{Subject: user("ada"), Relation: "staff", Object: Entity{}},
	} {
		if err := s.Grant(tup); err != ErrInvalid {
			t.Fatalf("%+v: want ErrInvalid, got %v", tup, err)
		}
	}
}

// The platform rule, end to end: facility staff reach their facility's
// patients; guardians reach their own; everyone else is denied.
func TestCheckerPatientAccess(t *testing.T) {
	s := NewMemoryStore()
	c := &Checker{Store: s}
	p := PatientRef{ID: "P1", FacilityID: "F1"}

	// Unrelated user: denied.
	ok, _ := c.CanAccessPatient("stranger", p)
	if ok {
		t.Fatal("stranger allowed")
	}
	// Staff of the registering facility: allowed.
	s.Grant(Tuple{Subject: user("nurse-a"), Relation: "staff", Object: facility("F1")})
	ok, _ = c.CanAccessPatient("nurse-a", p)
	if !ok {
		t.Fatal("facility staff denied")
	}
	// Staff of a DIFFERENT facility: still denied.
	s.Grant(Tuple{Subject: user("nurse-b"), Relation: "staff", Object: facility("F2")})
	ok, _ = c.CanAccessPatient("nurse-b", p)
	if ok {
		t.Fatal("cross-facility staff allowed without referral")
	}
	// ... until a referral tuple exists.
	s.Grant(Tuple{Subject: user("nurse-b"), Relation: "referral", Object: patient("P1")})
	ok, _ = c.CanAccessPatient("nurse-b", p)
	if !ok {
		t.Fatal("referral grant denied")
	}
	// Guardian: direct grant, no facility needed.
	s.Grant(Tuple{Subject: user("mama"), Relation: "guardian", Object: patient("P1")})
	ok, _ = c.CanAccessPatient("mama", p)
	if !ok {
		t.Fatal("guardian denied")
	}
	// Unattributed access is never allowed.
	ok, _ = c.CanAccessPatient("", p)
	if ok {
		t.Fatal("empty user allowed")
	}
	// Unaffiliated record: only direct grants work.
	orphan := PatientRef{ID: "P2"}
	ok, _ = c.CanAccessPatient("nurse-a", orphan)
	if ok {
		t.Fatal("facility staff reached unaffiliated patient")
	}
	s.Grant(Tuple{Subject: user("nurse-a"), Relation: "care-team", Object: patient("P2")})
	ok, _ = c.CanAccessPatient("nurse-a", orphan)
	if !ok {
		t.Fatal("care-team grant on unaffiliated patient denied")
	}
}

func TestIsFacilityAdmin(t *testing.T) {
	s := NewMemoryStore()
	c := &Checker{Store: s}
	s.Grant(Tuple{Subject: user("boss"), Relation: "admin", Object: facility("F1")})
	s.Grant(Tuple{Subject: user("nurse"), Relation: "staff", Object: facility("F1")})
	ok, _ := c.IsFacilityAdmin("boss", "F1")
	if !ok {
		t.Fatal("admin not recognised")
	}
	ok, _ = c.IsFacilityAdmin("nurse", "F1")
	if ok {
		t.Fatal("staff is not admin")
	}
}
