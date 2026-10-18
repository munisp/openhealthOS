package main

// ADR-0018 integrity plane tests: decision store, dispute semantics,
// hash-chained audit (cross-verified by packages/auditlake — the Go
// writer and the Python verifier must agree byte-for-byte), the open
// grade formula, and the HTTP surface.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func mkClaim(id, member, facility string) Claim {
	return Claim{ClaimID: id, MemberID: member, ProviderID: facility, ServiceDate: "2026-10-18"}
}

func mkDecision(dec string, linked bool, presence string) Decision {
	return Decision{Decision: dec, PayableNGN: 500, Linkage: Linkage{Linked: linked, Presence: presence}}
}

func TestDecisionStoreDisputeIsIdempotent(t *testing.T) {
	s := NewDecisionStore()
	s.Put(mkClaim("C-1", "M-1", "F-1"), mkDecision("APPROVE", true, "card"))
	if !s.Dispute("C-1", "care never happened", "M-1") {
		t.Fatal("first dispute should succeed")
	}
	if s.Dispute("C-1", "again", "M-1") {
		t.Fatal("second dispute must not rewrite the first — the first accuser stands")
	}
	sc, _ := s.Get("C-1")
	if sc.DisputeReason != "care never happened" || sc.DisputedBy != "M-1" {
		t.Fatalf("dispute overwritten: %+v", sc)
	}
	if s.Dispute("C-404", "x", "y") {
		t.Fatal("unknown claim must not dispute")
	}
}

func TestDecisionStoreForMemberSortedDesc(t *testing.T) {
	s := NewDecisionStore()
	s.Put(mkClaim("C-1", "M-1", "F-1"), mkDecision("APPROVE", true, "card"))
	s.Put(mkClaim("C-2", "M-1", "F-2"), mkDecision("PEND", false, ""))
	s.Put(mkClaim("C-3", "M-2", "F-1"), mkDecision("DENY", true, "biometric"))
	got := s.ForMember("M-1")
	if len(got) != 2 {
		t.Fatalf("want 2 claims for M-1, got %d", len(got))
	}
	for _, sc := range got {
		if sc.Claim.MemberID != "M-1" {
			t.Fatalf("leaked another member's claim: %+v", sc.Claim)
		}
	}
}

// The load-bearing test: Go writes the chain, packages/auditlake
// verifies it. If these two ever disagree, the audit file is fiction.
func TestAuditChainCrossVerifiedByAuditlake(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	c, err := NewAuditChain(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := c.Append("claims-orchestrator", "claim_decision", "C-1",
		map[string]any{"decision": "APPROVE", "payable_ngn": 500, "linked": true}); err != nil {
		t.Fatal(err)
	}
	if err := c.Append("officer-7", "enrol_member", "NG-T1",
		map[string]any{"programme": "BHCPF"}); err != nil {
		t.Fatal(err)
	}
	// Reopen: the chain must resume from the last hash, not the genesis.
	c2, err := NewAuditChain(path)
	if err != nil {
		t.Fatal(err)
	}
	if c2.prev == auditGenesis {
		t.Fatal("chain did not resume from existing file")
	}
	if err := c2.Append("M-1", "claim_disputed", "C-1", map[string]any{"reason": "no visit"}); err != nil {
		t.Fatal(err)
	}
	verifier := "../../packages/auditlake/auditlake.py"
	if _, err := os.Stat(verifier); err != nil {
		t.Skip("auditlake verifier not found")
	}
	out, err := exec.Command("python3", verifier, "verify", path).CombinedOutput()
	if err != nil {
		t.Fatalf("auditlake rejected Go-written chain: %v\n%s", err, out)
	}
	// Tamper: flip one byte in a stored line — verification must fail.
	raw, _ := os.ReadFile(path)
	tampered := strings.Replace(string(raw), "APPROVE", "APPROVE!", 1)
	tamperedPath := path + ".tampered"
	if err := os.WriteFile(tamperedPath, []byte(tampered), 0o600); err != nil {
		t.Fatal(err)
	}
	if out, err := exec.Command("python3", verifier, "verify", tamperedPath).CombinedOutput(); err == nil {
		t.Fatalf("auditlake accepted a tampered chain: %s", out)
	}
}

func TestGradeIntegrityFormula(t *testing.T) {
	clean := &IntegrityReport{Claims: 20, Approved: 20, BiometricShare: 0.5, PendRate: 0.1}
	if g := gradeIntegrity(clean); g != "A" {
		t.Fatalf("clean facility: want A, got %s", g)
	}
	ghosty := &IntegrityReport{Claims: 20, Disputed: 2, LinkageFailures: 3, PendRate: 0.4, BiometricShare: 0.1}
	// 100 − 50 (disputes) − 30 (linkage) − 15 (pend) − 10 (biometric) = −5 → 0 → D
	if g := gradeIntegrity(ghosty); g != "D" {
		t.Fatalf("ghost-claiming facility: want D, got %s", g)
	}
	borderline := &IntegrityReport{Claims: 5, LinkageFailures: 2, PendRate: 0.2, BiometricShare: 0.0}
	// 100 − 20 = 80 → B (biometric rule needs ≥10 claims)
	if g := gradeIntegrity(borderline); g != "B" {
		t.Fatalf("borderline facility: want B, got %s", g)
	}
}

func TestIntegrityHTTPSurface(t *testing.T) {
	store := NewDecisionStore()
	audit, err := NewAuditChain(filepath.Join(t.TempDir(), "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	registerIntegrityRoutes(mux, store, audit, eventPoster{})

	store.Put(mkClaim("C-1", "M-1", "F-1"), mkDecision("APPROVE", true, "biometric"))
	store.Put(mkClaim("C-2", "M-1", "F-1"), mkDecision("PEND", false, ""))

	// Member claims list.
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/claims/for-member?member_id=M-1", nil))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "C-1") {
		t.Fatalf("for-member: %d %s", rec.Code, rec.Body)
	}

	// Dispute: anonymous refused, attributed accepted, repeat idempotent.
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/claims/C-1/dispute",
		strings.NewReader(`{"actor":"","reason":""}`)))
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("anonymous dispute must be refused: %d", rec.Code)
	}
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/claims/C-1/dispute",
		strings.NewReader(`{"actor":"M-1","reason":"I was never at this facility"}`)))
	if rec.Code != 200 {
		t.Fatalf("dispute: %d %s", rec.Code, rec.Body)
	}
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/claims/C-404/dispute",
		strings.NewReader(`{"actor":"M-1","reason":"x"}`)))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown claim dispute must 404: %d", rec.Code)
	}

	// Scorecard: 1 disputed (−25) + 1 linkage failure (−10) = 65 → C.
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/integrity/F-1", nil))
	var rep IntegrityReport
	if err := json.Unmarshal(rec.Body.Bytes(), &rep); err != nil {
		t.Fatal(err)
	}
	if rep.Claims != 2 || rep.Disputed != 1 || rep.LinkageFailures != 1 || rep.Grade != "C" {
		t.Fatalf("scorecard: %+v", rep)
	}
	// The dispute must be in the chain.
	raw, _ := os.ReadFile(audit.path)
	if !strings.Contains(string(raw), `"action":"claim_disputed"`) {
		t.Fatalf("dispute not audited: %s", raw)
	}
}
