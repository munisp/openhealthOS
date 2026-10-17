package main

import (
	"errors"
	"strings"
	"testing"
	"time"
)

// staticConsent is the test checker: grants listed as "patient|facility".
type staticConsent struct{ grants map[string]bool }

func (c staticConsent) Check(patientID, grantee, _ string) (bool, error) {
	return c.grants[patientID+"|"+grantee], nil
}

type failingConsent struct{}

func (failingConsent) Check(_, _, _ string) (bool, error) {
	return false, errors.New("consent service down")
}

func newStore() *Store {
	s := NewStore(staticConsent{grants: map[string]bool{
		"OH-NG-KD-0014-000123|NG-KD-0099": true, // one granted cross-facility pair
	}})
	s.now = func() time.Time { return time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC) }
	return s
}

func seed(t *testing.T, s *Store) {
	t.Helper()
	err := s.Put(Resource{
		ResourceType: "Observation", ID: "obs-1",
		PatientID: "OH-NG-KD-0014-000123", SourceFacility: "NG-KD-0014",
		Body: []byte(`{"resourceType":"Observation","id":"obs-1","status":"final"}`),
	})
	if err != nil {
		t.Fatalf("seed: %v", err)
	}
}

func TestSameFacilityReadAllowed(t *testing.T) {
	s := newStore()
	seed(t, s)
	r, err := s.Get("Observation", "obs-1", "NG-KD-0014", false)
	if err != nil || !strings.Contains(string(r.Body), `"status":"final"`) {
		t.Fatalf("same-facility read: %v %+v", err, r)
	}
}

func TestCrossFacilityWithoutConsentDenied(t *testing.T) {
	s := newStore()
	seed(t, s)
	_, err := s.Get("Observation", "obs-1", "NG-KD-0055", false)
	if !errors.Is(err, ErrNoConsent) {
		t.Fatalf("want ErrNoConsent, got %v", err)
	}
	// The denial itself is an audit event — a denied read must leave a trace.
	audit := s.Audit()
	last := audit[len(audit)-1]
	if last.Decision != "denied" || last.Basis != "no-consent" || last.Actor != "NG-KD-0055" {
		t.Fatalf("denial not audited: %+v", last)
	}
}

func TestCrossFacilityWithConsentAllowed(t *testing.T) {
	s := newStore()
	seed(t, s)
	if _, err := s.Get("Observation", "obs-1", "NG-KD-0099", false); err != nil {
		t.Fatalf("consented read denied: %v", err)
	}
	last := s.Audit()[len(s.Audit())-1]
	if last.Decision != "allowed" || last.Basis != "consent" {
		t.Fatalf("consent basis not audited: %+v", last)
	}
}

func TestBreakGlassAllowedAndFlagged(t *testing.T) {
	s := newStore()
	seed(t, s)
	if _, err := s.Get("Observation", "obs-1", "NG-KD-0055", true); err != nil {
		t.Fatalf("break-glass denied: %v", err)
	}
	last := s.Audit()[len(s.Audit())-1]
	if last.Decision != "break-glass" || last.Basis != "emergency" {
		t.Fatalf("break-glass not flagged: %+v", last)
	}
}

func TestConsentServiceDownFailsClosed(t *testing.T) {
	s := NewStore(failingConsent{})
	seed(t, s)
	_, err := s.Get("Observation", "obs-1", "NG-KD-0099", false)
	if !errors.Is(err, ErrNoConsent) {
		t.Fatalf("consent outage must fail closed, got %v", err)
	}
}

func TestPatientEverythingUnderOneDecision(t *testing.T) {
	s := newStore()
	seed(t, s)
	s.Put(Resource{ResourceType: "Encounter", ID: "enc-1",
		PatientID: "OH-NG-KD-0014-000123", SourceFacility: "NG-KD-0014",
		Body: []byte(`{"resourceType":"Encounter","id":"enc-1"}`)})
	s.Put(Resource{ResourceType: "Observation", ID: "obs-other",
		PatientID: "OH-NG-KD-0014-999999", SourceFacility: "NG-KD-0014",
		Body: []byte(`{"resourceType":"Observation","id":"obs-other"}`)})
	all, err := s.PatientEverything("OH-NG-KD-0014-000123", "NG-KD-0099", false)
	if err != nil || len(all) != 2 {
		t.Fatalf("everything: %v len=%d", err, len(all))
	}
	// Other patient's resources never leak into the bundle.
	for _, r := range all {
		if r.PatientID != "OH-NG-KD-0014-000123" {
			t.Fatalf("leak: %+v", r)
		}
	}
}

func TestPutValidation(t *testing.T) {
	s := newStore()
	if err := s.Put(Resource{ResourceType: "Observation"}); !errors.Is(err, ErrBadRequest) {
		t.Fatalf("want ErrBadRequest, got %v", err)
	}
}

func TestUnknownResource(t *testing.T) {
	s := newStore()
	if _, err := s.Get("Observation", "nope", "NG-KD-0014", false); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}
