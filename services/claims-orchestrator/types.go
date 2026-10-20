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
	// EncounterID is the linkage evidence (ADR-0018): the platform
	// encounter this claim bills against. Missing or unmatched → the
	// adjudicator pends for human review. Money follows encounters.
	EncounterID string `json:"encounter_id,omitempty"`
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
	// Linkage reports the encounter-evidence verdict (ADR-0018) —
	// the integrity scorecard counts on it.
	Linkage Linkage `json:"linkage"`
}

// Linkage is the encounter-evidence verdict: was the claim tied to a
// matching platform encounter, and how strongly was presence proven.
type Linkage struct {
	Linked   bool   `json:"linked"`
	Presence string `json:"presence,omitempty"` // biometric | card | self
}

// ClaimResponse is what the orchestrator returns to the caller.
type ClaimResponse struct {
	ClaimID    string   `json:"claim_id"`
	Decision   string   `json:"decision"`
	// Fraud gate outcomes (innovation 7) — visible to the caller, never silent.
	FraudScore   float64  `json:"fraud_score,omitempty"`
	FraudReasons []string `json:"fraud_reasons,omitempty"`
	FraudNote    string   `json:"fraud_note,omitempty"`
	Reasons    []string `json:"reasons"`
	Flags      []string `json:"flags"`
	AllowedNGN int      `json:"allowed_ngn"`
	CopayNGN   int      `json:"copay_ngn"`
	PayableNGN int      `json:"payable_ngn"`
	// Linkage is the encounter-evidence verdict (ADR-0018) — the caller
	// sees WHY a claim pended, not just that it did.
	Linkage         Linkage  `json:"linkage"`
	Ledger          []string `json:"ledger,omitempty"`
	LedgerError     string   `json:"ledger_error,omitempty"`
	Settlement      any      `json:"settlement,omitempty"`
	SettlementError string   `json:"settlement_error,omitempty"`
}
