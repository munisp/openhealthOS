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
type claimsHandler struct {
	Adjudicator Adjudicator
	Ledger      Ledger
	Switch      Switch
	Tally       *PayerTally
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
	resp := ClaimResponse{
		ClaimID: dec.ClaimID, Decision: dec.Decision,
		Reasons: dec.Reasons, Flags: dec.Flags,
		AllowedNGN: dec.AllowedNGN, CopayNGN: dec.CopayNGN, PayableNGN: dec.PayableNGN,
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
