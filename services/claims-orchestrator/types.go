// Package main — OpenHealth OS claims orchestration service.
//
// The money path, end to end:
//
//	claim (JSON) ─► adjudicator (eligibility, benefit, tariff, fraud flags)
//	              ─► ledger (double-entry posting, minor units, 2PC seam)
//	              ─► settlement switch (Mojaloop-pattern FSPIOP /quotes + /transfers)
//
// Every dependency is behind an interface so the pipeline is conformance-tested
// without subprocesses or network. Production wiring uses the subprocess and
// HTTP implementations; tests use fakes.
package main

// Claim is the inbound claim contract (claims-gateway submit shape, JSON form).
type Claim struct {
	ClaimID     string `json:"claim_id"`
	MemberID    string `json:"member_id"`
	ProviderID  string `json:"provider_id"`
	ServiceDate string `json:"service_date"` // YYYY-MM-DD
	Items       []Item `json:"items"`
	ClaimedNGN  int    `json:"claimed_ngn"`
	// History carries recent prior claims for duplicate detection (14-day window).
	History []Claim `json:"history,omitempty"`
}

// Item is one claimed service line (terminology code + quantity).
type Item struct {
	Code string `json:"code"`
	Qty  int    `json:"qty"`
}

// Decision is the adjudicator's output. Every rule that fires leaves a reason
// or flag code — adjudications are auditable events, never silent verdicts.
type Decision struct {
	ClaimID    string   `json:"claim_id"`
	Decision   string   `json:"decision"` // APPROVE | PEND | DENY
	Member     string   `json:"member,omitempty"`
	Scheme     string   `json:"scheme,omitempty"`
	Reasons    []string `json:"reasons"`
	Flags      []string `json:"flags"`
	AllowedNGN int      `json:"allowed_ngn"`
	CopayNGN   int      `json:"copay_ngn"`
	PayableNGN int      `json:"payable_ngn"`
}

// ClaimResponse is what the orchestrator returns to the caller.
type ClaimResponse struct {
	ClaimID         string   `json:"claim_id"`
	Decision        string   `json:"decision"`
	Reasons         []string `json:"reasons"`
	Flags           []string `json:"flags"`
	AllowedNGN      int      `json:"allowed_ngn"`
	CopayNGN        int      `json:"copay_ngn"`
	PayableNGN      int      `json:"payable_ngn"`
	Ledger          []string `json:"ledger,omitempty"`
	LedgerError     string   `json:"ledger_error,omitempty"`
	Settlement      any      `json:"settlement,omitempty"`
	SettlementError string   `json:"settlement_error,omitempty"`
}
