package main

import (
	"encoding/json"
	"net/http"
	"strings"
)

// Settlement account identifiers on the ledger seam (ADR-0003).
// Production maps these to chart-of-accounts codes per state scheme.
const (
	acctSettlePool    = "SETTLE-POOL"
	acctProviderFloat = "PROVIDER-FLOAT"
)

// claimsHandler wires adjudication → ledger → settlement for one claim.
// Tally, when set, folds every adjudication into the payer stats view.
// Store/Audit/Poster are the anti-collusion plane (ADR-0018): every
// decision is remembered (portal + scorecard), chained (tamper-evident
// audit), and told to the patient (claim_submitted SMS).
type claimsHandler struct {
	Adjudicator Adjudicator
	Ledger      Ledger
	Switch      Switch
	Tally       *PayerTally
	Store       *DecisionStore
	Audit       *AuditChain
	Poster      eventPoster
	// Fraud gate (innovation 7): scores APPROVEs before money moves.
	// nil scorer = gate off (FRAUD_HOOK=off on the edge).
	Fraud       FraudScorer
	FraudHoldAt float64 // default 0.7 when zero and Fraud set
}

func (h claimsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var c Claim
	if err := json.NewDecoder(r.Body).Decode(&c); err != nil {
		http.Error(w, "bad claim: "+err.Error(), http.StatusBadRequest)
		return
	}
	dec, err := h.Adjudicator.Adjudicate(c)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if h.Tally != nil {
		h.Tally.Record(dec)
	}
	// Anti-collusion plane (ADR-0018): remember, chain, and tell the
	// patient. A claim the patient never hears about is the ghost-claim
	// vector; a claim the patient sees is a distributed audit.
	if h.Store != nil {
		h.Store.Put(c, dec)
	}
	if h.Audit != nil {
		auditErr("claim_decision", h.Audit.Append("claims-orchestrator", "claim_decision", c.ClaimID, map[string]any{
			"decision": dec.Decision, "member_id": c.MemberID, "facility_id": c.ProviderID,
			"payable_ngn": dec.PayableNGN, "linked": dec.Linkage.Linked,
		}))
	}
	if dec.Decision != "DENY" {
		go h.Poster.fire("claim_submitted", map[string]any{
			"event_id": "claim-" + c.ClaimID, "facility_id": c.ProviderID,
			"member_id": c.MemberID, "claim_id": c.ClaimID,
			"amount_ngn": dec.PayableNGN, "service_date": c.ServiceDate,
			"decision": dec.Decision,
		})
	}
	resp := ClaimResponse{
		ClaimID: dec.ClaimID, Decision: dec.Decision,
		Reasons: dec.Reasons, Flags: dec.Flags,
		AllowedNGN: dec.AllowedNGN, CopayNGN: dec.CopayNGN, PayableNGN: dec.PayableNGN,
		Linkage: dec.Linkage,
	}
	// Pre-payment fraud gate (innovation 7): an APPROVE about to move money
	// is scored first. Hot claims pend for human review — the scorer never
	// denies. A slow/dead scorer fails OPEN with an audit note: payment
	// continuity outranks one review, and the batch sweep still sees it.
	if dec.Decision == "APPROVE" && dec.PayableNGN > 0 && h.Fraud != nil {
		claimMap := map[string]any{
			"claim_id": c.ClaimID, "member_id": c.MemberID, "provider_id": c.ProviderID,
			"amount_ngn": dec.PayableNGN, "tariff_code": firstCode(c), "ts": c.ServiceDate,
		}
		verdict, err := h.Fraud.Score(claimMap, historyForScoring(h.Store, c.ProviderID))
		holdAt := h.FraudHoldAt
		if holdAt == 0 {
			holdAt = 0.7
		}
		if err != nil {
			resp.FraudNote = "scorer unavailable — paid under fail-open policy; batch sweep will review"
			if h.Audit != nil {
				auditErr("fraud_gate_failopen", h.Audit.Append("claims-orchestrator", "fraud_gate_failopen", c.ClaimID, map[string]any{"error": err.Error()}))
			}
		} else if verdict.Score >= holdAt || verdict.Action == "PEND" {
			dec.Decision = "PEND_REVIEW"
			resp.FraudScore = verdict.Score
			resp.FraudReasons = verdict.Reasons
			resp.Decision = dec.Decision
			if h.Audit != nil {
				auditErr("fraud_hold", h.Audit.Append("claims-orchestrator", "fraud_hold", c.ClaimID, map[string]any{"score": verdict.Score, "reasons": verdict.Reasons}))
			}
		}
	}
	// Only APPROVE with a payable amount moves money. PEND queues for human
	// review; DENY is an auditable terminal state. Zero unaccounted outcomes.
	if dec.Decision == "APPROVE" && dec.PayableNGN > 0 {
		if out, err := h.Ledger.Post(acctSettlePool, acctProviderFloat, c.ClaimID, dec.PayableNGN); err == nil {
			resp.Ledger = strings.Split(strings.TrimSpace(out), "\n")
		} else {
			resp.LedgerError = err.Error()
		}
		if tr, err := h.Switch.Settle(c.ClaimID, c.ProviderID, dec.PayableNGN); err == nil {
			resp.Settlement = tr
		} else {
			resp.SettlementError = err.Error()
		}
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}
