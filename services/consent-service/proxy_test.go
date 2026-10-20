package main

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"
)

func TestProxyLifecycle(t *testing.T) {
	m := NewProxyManager([]byte("k"))
	m.now = func() time.Time { return time.Date(2026, 10, 20, 0, 0, 0, 0, time.UTC) }
	g, err := m.IssueProxy("P-CHILD", "P-MOTHER", "parent", "record:read,appointments:book,collect:medication",
		time.Date(2027, 10, 20, 0, 0, 0, 0, time.UTC))
	if err != nil {
		t.Fatal(err)
	}
	ok, err := m.CheckProxy("P-CHILD", "P-MOTHER", "record:read")
	if !ok || err != nil {
		t.Fatalf("expected allowed, got %v %v", ok, err)
	}
	ok, _ = m.CheckProxy("P-CHILD", "P-MOTHER", "claims:submit")
	if ok {
		t.Fatal("right outside scope must be denied")
	}
	if _, err := m.CheckProxy("P-CHILD", "P-STRANGER", "record:read"); err != ErrNotFound {
		t.Fatalf("stranger must be not-found, got %v", err)
	}
	if err := m.RevokeProxy(g.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := m.CheckProxy("P-CHILD", "P-MOTHER", "record:read"); err != ErrRevoked {
		t.Fatalf("revoked must fail closed, got %v", err)
	}
}

func TestProxyDelegationRefused(t *testing.T) {
	m := NewProxyManager([]byte("k"))
	_, err := m.IssueProxy("P1", "P2", "guardian", "record:read,consent:manage", time.Now().Add(time.Hour))
	if err != ErrDelegation {
		t.Fatalf("delegation must be refused, got %v", err)
	}
}

func TestProxyExpiryFailClosed(t *testing.T) {
	m := NewProxyManager([]byte("k"))
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	m.now = func() time.Time { return now }
	g, _ := m.IssueProxy("P1", "P2", "parent", "record:read", now.Add(24*time.Hour))
	_ = g
	now = now.Add(48 * time.Hour)
	if _, err := m.CheckProxy("P1", "P2", "record:read"); err != ErrRevoked {
		t.Fatalf("expired must fail closed, got %v", err)
	}
}

func TestProxyTamperDetected(t *testing.T) {
	m := NewProxyManager([]byte("k"))
	m.now = func() time.Time { return time.Date(2026, 10, 20, 0, 0, 0, 0, time.UTC) }
	g, _ := m.IssueProxy("P1", "P2", "parent", "record:read", time.Date(2027, 1, 1, 0, 0, 0, 0, time.UTC))
	g.Scope = "record:read,claims:submit" // tamper in place
	m.byID[g.ID] = &g
	if _, err := m.CheckProxy("P1", "P2", "claims:submit"); err != ErrTampered {
		t.Fatalf("tamper must fail closed, got %v", err)
	}
}

func TestProxyHTTPEndpoints(t *testing.T) {
	s := server{m: NewManager([]byte("k")), px: NewProxyManager([]byte("k"))}
	mux := s.routes()
	body, _ := json.Marshal(map[string]string{
		"patient_id": "P-CHILD", "proxy_id": "P-MOTHER", "relationship": "parent",
		"scope": "record:read", "expires_at": time.Now().Add(24 * time.Hour).UTC().Format(time.RFC3339)})
	r := httptest.NewRequest("POST", "/proxy", bytes.NewReader(body))
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, r)
	if w.Code != 201 {
		t.Fatalf("issue: %d %s", w.Code, w.Body)
	}
	chk, _ := json.Marshal(map[string]string{"patient_id": "P-CHILD", "proxy_id": "P-MOTHER", "right": "record:read"})
	r = httptest.NewRequest("POST", "/proxy/check", bytes.NewReader(chk))
	w = httptest.NewRecorder()
	mux.ServeHTTP(w, r)
	var out map[string]any
	json.Unmarshal(w.Body.Bytes(), &out)
	if out["allowed"] != true {
		t.Fatalf("check: %v", out)
	}
}
