package main

import (
	"errors"
	"path/filepath"
	"testing"
)

type fakeFraud struct {
	verdict FraudVerdict
	err     error
	calls   int
}

func (f *fakeFraud) Score(claim map[string]any, history []map[string]any) (FraudVerdict, error) {
	f.calls++
	return f.verdict, f.err
}

func claimBody() string {
	return `{"claim_id":"C-1","member_id":"M-1","provider_id":"F-1","service_date":"2026-10-20","claimed_ngn":5000,"items":[{"code":"CONSULT","qty":1}]}`
}

type approveAdj struct{}

func (approveAdj) Adjudicate(c Claim) (Decision, error) {
	return Decision{ClaimID: c.ClaimID, Decision: "APPROVE", PayableNGN: 5000}, nil
}

var errFake = errors.New("fake scorer")

func TestFraudGatePendsHotClaimBeforeMoneyMoves(t *testing.T) {
	ff := &fakeFraud{verdict: FraudVerdict{Action: "PEND", Score: 0.9, Reasons: []string{"UPCODING_Z amount z=9.1"}}}
	ledger := &fakeLedger{}
	h := claimsHandler{Adjudicator: approveAdj{}, Ledger: ledger, Switch: &fakeSwitch{}, Store: NewDecisionStore(), Fraud: ff}
	_, resp := postClaim(t, h, claimBody())
	if resp.Decision != "PEND_REVIEW" {
		t.Fatalf("hot claim must pend, got %s", resp.Decision)
	}
	if len(ledger.posts) != 0 {
		t.Fatal("money must NOT move on a fraud hold")
	}
	if resp.FraudScore != 0.9 {
		t.Fatalf("score must be visible to the caller: %+v", resp)
	}
}

func TestFraudGateClearClaimPays(t *testing.T) {
	ff := &fakeFraud{verdict: FraudVerdict{Action: "CLEAR", Score: 0.1}}
	ledger := &fakeLedger{}
	h := claimsHandler{Adjudicator: approveAdj{}, Ledger: ledger, Switch: &fakeSwitch{}, Store: NewDecisionStore(), Fraud: ff}
	_, resp := postClaim(t, h, claimBody())
	if resp.Decision != "APPROVE" || len(ledger.posts) != 1 {
		t.Fatalf("clear claim must pay: %+v ledger=%d", resp, len(ledger.posts))
	}
	if ff.calls != 1 {
		t.Fatal("gate must score exactly once per approved payable claim")
	}
}

func TestFraudGateFailsOpenWithAuditNote(t *testing.T) {
	ff := &fakeFraud{err: errFake}
	ledger := &fakeLedger{}
	ac, err := NewAuditChain(filepath.Join(t.TempDir(), "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	h := claimsHandler{Adjudicator: approveAdj{}, Ledger: ledger, Switch: &fakeSwitch{}, Store: NewDecisionStore(), Audit: ac, Fraud: ff}
	_, resp := postClaim(t, h, claimBody())
	if resp.Decision != "APPROVE" || len(ledger.posts) != 1 {
		t.Fatalf("fail-open: payment must proceed, got %+v", resp)
	}
	if resp.FraudNote == "" {
		t.Fatal("fail-open must carry an audit note")
	}
}

func TestSubprocessScorerRealCLI(t *testing.T) {
	s := SubprocessFraudScorer{Script: resolveToolPath("../../packages/frauddetect/frauddetect.py")}
	v, err := s.Score(
		map[string]any{"claim_id": "C-9", "provider_id": "F-1", "amount_ngn": 900000, "tariff_code": "T", "ts": "2026-10-20"},
		[]map[string]any{{"claim_id": "C-0", "provider_id": "F-1", "amount_ngn": 1000, "tariff_code": "T", "ts": "2026-10-19"}},
	)
	if err != nil {
		t.Fatalf("CLI scorer: %v", err)
	}
	if v.Action != "CLEAR" && v.Action != "PEND" {
		t.Fatalf("bad action %q", v.Action)
	}
}
