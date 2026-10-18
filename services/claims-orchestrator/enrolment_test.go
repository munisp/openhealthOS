package main

// Enrolment endpoint tests (ADR-0017) — handler-level with a fake
// Enroller, plus one real subprocess round-trip against the actual
// adjudicator (the enrolment store is the integration's whole point).

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type fakeEnroller struct {
	res map[string]any
	err error
	got string
}

func (f *fakeEnroller) Enrol(record json.RawMessage) (map[string]any, error) {
	f.got = string(record)
	return f.res, f.err
}

func (f *fakeEnroller) Suspend(memberID, reason string) (map[string]any, error) {
	f.got = memberID + "|" + reason
	return f.res, f.err
}

func TestEnrolmentEndpointEnrols(t *testing.T) {
	fe := &fakeEnroller{res: map[string]any{"member_id": "NG-T1", "active": true}}
	h := enrolmentHandler{Enroller: fe}
	req := httptest.NewRequest(http.MethodPost, "/enrolments",
		strings.NewReader(`{"member_id":"NG-T1","name":"A","scheme":"BHCPF-VUL","valid_until":"2027-12-31"}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), `"active":true`) {
		t.Fatalf("enrol: %d %s", rec.Code, rec.Body)
	}
	if !strings.Contains(fe.got, "BHCPF-VUL") {
		t.Fatalf("record not forwarded: %q", fe.got)
	}
}

func TestEnrolmentSuspendIsVerbFirst(t *testing.T) {
	fe := &fakeEnroller{res: map[string]any{"member_id": "NG-T1", "active": false}}
	h := enrolmentHandler{Enroller: fe}
	req := httptest.NewRequest(http.MethodPost, "/enrolments",
		strings.NewReader(`{"action":"suspend","member_id":"NG-T1","reason":"duplicate identity"}`))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 200 || fe.got != "NG-T1|duplicate identity" {
		t.Fatalf("suspend: %d %q", rec.Code, fe.got)
	}
}

// Real subprocess: enrol → eligible, suspend → ineligible with reason.
func TestEnrolmentSubprocessRoundTrip(t *testing.T) {
	script := "../../packages/adjudicator/adjudicate.py"
	if _, err := os.Stat(script); err != nil {
		t.Skip("adjudicator script not found")
	}
	members := filepath.Join(t.TempDir(), "members.json")
	t.Setenv("MEMBERS_FILE", members)
	adj := SubprocessAdjudicator{Python: "python3", Script: script}

	out, err := adj.Enrol(json.RawMessage(
		`{"member_id":"NG-E2E","name":"E2E Patient","scheme":"BHCPF-VUL","valid_until":"2027-12-31","biometric_required":true}`))
	if err != nil || out["active"] != true {
		t.Fatalf("enrol: %v %+v", err, out)
	}
	chk, err := adj.CheckEligibility("NG-E2E", "2026-10-18")
	if err != nil || chk["eligible"] != true || chk["biometric_required"] != true {
		t.Fatalf("check after enrol: %v %+v", err, chk)
	}
	if _, err := adj.Suspend("NG-E2E", "e2e test suspension"); err != nil {
		t.Fatalf("suspend: %v", err)
	}
	chk, err = adj.CheckEligibility("NG-E2E", "2026-10-18")
	if err != nil || chk["eligible"] != false ||
		!strings.Contains(chk["reason"].(string), "e2e test suspension") {
		t.Fatalf("check after suspend: %v %+v", err, chk)
	}
}
