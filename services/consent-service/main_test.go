package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestIssueSignVerify(t *testing.T) {
	m := NewManager([]byte("test-key"))
	c, err := m.Issue("PT-1", "PHC-012", "record:read", "care-continuity")
	if err != nil {
		t.Fatal(err)
	}
	if c.Status != "active" || c.Signature == "" {
		t.Fatalf("bad artefact: %+v", c)
	}
	if !m.Verify(&c) {
		t.Fatal("fresh artefact must verify")
	}
}

func TestTamperingInvalidates(t *testing.T) {
	m := NewManager([]byte("test-key"))
	c, _ := m.Issue("PT-1", "PHC-012", "record:read", "care-continuity")
	c.Scope = "record:write" // attacker broadens scope
	if m.Verify(&c) {
		t.Fatal("tampered artefact must not verify")
	}
}

func TestRevocationIsImmediate(t *testing.T) {
	m := NewManager([]byte("test-key"))
	c, _ := m.Issue("PT-1", "PHC-012", "record:read", "care-continuity")
	ok, _ := m.Check("PT-1", "PHC-012", "record:read")
	if !ok {
		t.Fatal("active consent must allow")
	}
	if err := m.Revoke(c.ID); err != nil {
		t.Fatal(err)
	}
	ok, _ = m.Check("PT-1", "PHC-012", "record:read")
	if ok {
		t.Fatal("revoked consent must deny immediately")
	}
	// revoke is idempotent
	if err := m.Revoke(c.ID); err != nil {
		t.Fatal(err)
	}
}

func TestNoConsentNoAccess(t *testing.T) {
	m := NewManager([]byte("test-key"))
	ok, _ := m.Check("PT-9", "PHC-999", "record:read")
	if ok {
		t.Fatal("absence of consent must deny")
	}
}

func TestAuditChainCoversLifecycle(t *testing.T) {
	m := NewManager([]byte("test-key"))
	c, _ := m.Issue("PT-1", "PHC-012", "record:read", "care-continuity")
	m.Check("PT-1", "PHC-012", "record:read")
	m.Revoke(c.ID)
	events := []string{}
	for _, e := range m.Audit() {
		events = append(events, e.Event)
	}
	want := []string{"ISSUE", "CHECK", "REVOKE"}
	if strings.Join(events, ",") != strings.Join(want, ",") {
		t.Fatalf("audit chain %v, want %v", events, want)
	}
}

func TestHTTPLifecycle(t *testing.T) {
	s := server{m: NewManager([]byte("k"))}
	req := httptest.NewRequest(http.MethodPost, "/consents",
		strings.NewReader(`{"patient_id":"PT-1","grantee":"PHC-012","scope":"record:read","purpose":"care"}`))
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if rec.Code != 201 {
		t.Fatalf("issue: %d", rec.Code)
	}
	var c Consent
	json.Unmarshal(rec.Body.Bytes(), &c)

	req = httptest.NewRequest(http.MethodPost, "/consents/"+c.ID+"/revoke", nil)
	rec = httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("revoke: %d", rec.Code)
	}

	req = httptest.NewRequest(http.MethodPost, "/consents/check",
		strings.NewReader(`{"patient_id":"PT-1","grantee":"PHC-012","scope":"record:read"}`))
	rec = httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if !strings.Contains(rec.Body.String(), `"allowed":false`) {
		t.Fatalf("post-revoke check must deny: %s", rec.Body)
	}
}

func TestForPatientListsNewestFirst(t *testing.T) {
	m := NewManager([]byte("k"))
	c1, _ := m.Issue("P-1", "FAC-A", "record:read", "care")
	c2, _ := m.Issue("P-1", "FAC-B", "claims:submit", "reimbursement")
	m.Issue("P-2", "FAC-A", "record:read", "care") // another patient's — excluded
	got := m.ForPatient("P-1")
	if len(got) != 2 {
		t.Fatalf("want 2 consents, got %d", len(got))
	}
	if got[0].ID != c2.ID || got[1].ID != c1.ID {
		t.Fatalf("newest first: %+v", got)
	}
	m.Revoke(c2.ID)
	got = m.ForPatient("P-1")
	if got[0].Status != "revoked" {
		t.Fatalf("revocation visible in listing: %+v", got[0])
	}
}
