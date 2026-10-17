package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// --- fakes: the pipeline is conformance-tested without subprocesses/network ---

type fakeAdjudicator struct{ dec Decision }

func (f fakeAdjudicator) Adjudicate(Claim) (Decision, error) { return f.dec, nil }

type fakeLedger struct {
	posts []string
	err   error
}

func (f *fakeLedger) Post(debit, credit, tx string, amount int) (string, error) {
	f.posts = append(f.posts, tx)
	if f.err != nil {
		return "", f.err
	}
	return "OK: posted tx " + tx, nil
}

type fakeSwitch struct {
	calls int
	err   error
}

func (f *fakeSwitch) Settle(claimID, payee string, amount int) (map[string]any, error) {
	f.calls++
	if f.err != nil {
		return nil, f.err
	}
	return map[string]any{"status": "COMPLETED"}, nil
}

func postClaim(t *testing.T, h claimsHandler, body string) (int, ClaimResponse) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/claims", strings.NewReader(body))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var resp ClaimResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil && rec.Code == 200 {
		t.Fatalf("response not decodable: %v", err)
	}
	return rec.Code, resp
}

const validClaim = `{"claim_id":"C-1","member_id":"NG-0001","provider_id":"PHC-007","service_date":"2026-10-01","items":[{"code":"MAL-RDT","qty":1}],"claimed_ngn":1500}`

func TestApprovedClaimMovesMoney(t *testing.T) {
	led := &fakeLedger{}
	sw := &fakeSwitch{}
	h := claimsHandler{
		Adjudicator: fakeAdjudicator{dec: Decision{ClaimID: "C-1", Decision: "APPROVE", PayableNGN: 1500}},
		Ledger:      led,
		Switch:      sw,
	}
	code, resp := postClaim(t, h, validClaim)
	if code != 200 || resp.Decision != "APPROVE" {
		t.Fatalf("unexpected: code=%d resp=%+v", code, resp)
	}
	if len(led.posts) != 1 || sw.calls != 1 {
		t.Fatalf("approved claim must post ledger and settle once: posts=%v calls=%d", led.posts, sw.calls)
	}
}

func TestPendedClaimMovesNoMoney(t *testing.T) {
	led := &fakeLedger{}
	sw := &fakeSwitch{}
	h := claimsHandler{
		Adjudicator: fakeAdjudicator{dec: Decision{ClaimID: "C-2", Decision: "PEND", Flags: []string{"DUPLICATE"}}},
		Ledger:      led,
		Switch:      sw,
	}
	code, resp := postClaim(t, h, validClaim)
	if code != 200 || resp.Decision != "PEND" {
		t.Fatalf("unexpected: code=%d resp=%+v", code, resp)
	}
	if len(led.posts) != 0 || sw.calls != 0 {
		t.Fatal("PEND must never touch the ledger or the switch")
	}
}

func TestDeniedClaimMovesNoMoney(t *testing.T) {
	led := &fakeLedger{}
	sw := &fakeSwitch{}
	h := claimsHandler{
		Adjudicator: fakeAdjudicator{dec: Decision{ClaimID: "C-3", Decision: "DENY", Reasons: []string{"ELIGIBILITY"}}},
		Ledger:      led,
		Switch:      sw,
	}
	_, resp := postClaim(t, h, validClaim)
	if resp.Decision != "DENY" || len(led.posts) != 0 || sw.calls != 0 {
		t.Fatalf("DENY must be terminal and silent on money rails: %+v", resp)
	}
}

func TestLedgerFailureIsSurfacedNotSwallowed(t *testing.T) {
	led := &fakeLedger{err: errors.New("ledger unreachable")}
	sw := &fakeSwitch{}
	h := claimsHandler{
		Adjudicator: fakeAdjudicator{dec: Decision{ClaimID: "C-4", Decision: "APPROVE", PayableNGN: 100}},
		Ledger:      led,
		Switch:      sw,
	}
	_, resp := postClaim(t, h, validClaim)
	if resp.LedgerError == "" {
		t.Fatal("ledger error must be surfaced in the response, never swallowed")
	}
}

func TestRejectsNonPost(t *testing.T) {
	h := claimsHandler{}
	req := httptest.NewRequest(http.MethodGet, "/claims", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("GET must be rejected, got %d", rec.Code)
	}
}

func TestBuildQuoteAndTransferShape(t *testing.T) {
	q := buildQuote("C-100", "PHC-007", 4500, time.Now())
	if q["quoteId"] != "Q-C-100" || q["amount"] != "4500" || q["currency"] != "NGN" {
		t.Fatalf("unexpected quote: %v", q)
	}
	tr := buildTransfer(q)
	if tr["transferId"] != "TR-Q-C-100" || tr["ilpPacket"] == "" {
		t.Fatalf("unexpected transfer: %v", tr)
	}
}
