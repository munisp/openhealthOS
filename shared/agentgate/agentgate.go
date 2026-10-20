// Package agentgate — one policy-bound seam for every AI/agent action
// (ADR-0048).
//
// Pattern adopted clean-room from VERITAS (Apache-2.0,
// Chesterguan/veritas): agent runtimes can be fast and small, but in a
// regulated environment every action must be traceable, policy-
// constrained and verifiable. VERITAS's pipeline —
//
//	State → Policy → Capability → Propose → Verify → Transition → Audit
//
// — unifies doctrines we already hold in pieces (ADR-0045 eval gates,
// ADR-0046 write validation, fraud PEND = human review, auditlake hash
// chains) into ONE runtime contract: no agent action executes outside
// the gate.
//
// Trust boundary (theirs, and ours): the runtime core, policy engine,
// audit and verifier are trusted; the LLM, tools, input data and the
// external environment are not. propose() output is therefore untrusted
// and ALWAYS passes verify() before commit.
//
// Stdlib Go (ADR-0005). The audit chain uses the same law as
// packages/auditlake: sha256(canonical(entry) + prev_hash).
package agentgate

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"
)

// Policy is deny-by-default: an action not listed is denied. This is
// the single most important property — an unlisted action failing open
// is how "temporary" agent capabilities become permanent shadow ones.
type Policy struct {
	// Actions maps action name → rule. Missing action = deny.
	Actions map[string]Rule `json:"actions"`
}

type Rule struct {
	// Actors allowed to perform the action (agent identities, e.g.
	// "triage-llm"). Empty = nobody (deny-by-default within the rule).
	Actors []string `json:"actors"`
	// RequireApproval routes the proposal to a human before commit —
	// VERITAS's RequireApproval lifecycle, the same doctrine as fraud
	// PEND_REVIEW and step-up elevation: irreversible or clinical actions
	// are proposed by machines and committed by people.
	RequireApproval bool `json:"require_approval"`
	// MaxRisk is an optional ceiling: verify() scores above it deny even
	// for allowed actors.
	MaxRisk float64 `json:"max_risk"`
}

// Verdict is what verify() returns about an untrusted proposal.
type Verdict struct {
	OK     bool     `json:"ok"`
	Risk   float64  `json:"risk"` // 0..1
	Reason []string `json:"reason"`
}

// Verifier checks a proposal before commit. Platform verifiers:
// fhirguard (write validation), danger-sign context checks, claim
// consistency. Nil verifier = only schema sanity (presence of output).
type Verifier func(action string, input, proposal any) Verdict

// AuditEntry is hash-chained: same law as auditlake.
type AuditEntry struct {
	Seq       int    `json:"seq"`
	TS        string `json:"ts"`
	Actor     string `json:"actor"`
	Action    string `json:"action"`
	Decision  string `json:"decision"` // ALLOW | DENY | PEND_APPROVAL | REJECT_VERIFY
	Reason    string `json:"reason,omitempty"`
	PrevHash  string `json:"prev_hash"`
	Hash      string `json:"hash"`
}

const genesis = "0000000000000000000000000000000000000000000000000000000000000000"

func hashEntry(e AuditEntry) string {
	body := map[string]any{
		"seq": e.Seq, "ts": e.TS, "actor": e.Actor, "action": e.Action,
		"decision": e.Decision, "reason": e.Reason, "prev_hash": e.PrevHash,
	}
	buf, _ := json.Marshal(body) // encoding/json sorts map keys — canonical
	sum := sha256.Sum256(append(buf, []byte(e.PrevHash)...))
	return hex.EncodeToString(sum[:])
}

// Gate is the runtime. One per service; audit chain in memory with an
// optional Sink (e.g. auditlake append) for durability.
type Gate struct {
	policy  Policy
	verify  Verifier
	now     func() time.Time
	sink    func(AuditEntry)

	mu    sync.Mutex
	chain []AuditEntry
}

func New(policy Policy, verify Verifier, sink func(AuditEntry)) *Gate {
	return &Gate{
		policy: policy, verify: verify, sink: sink,
		now: func() time.Time { return time.Now().UTC() },
	}
}

// Decision is the gate's answer.
type Decision struct {
	Allowed  bool      `json:"allowed"`
	Pending  bool      `json:"pending"` // RequireApproval: proposal valid, awaiting human
	Output   any       `json:"output,omitempty"`
	Reason   string    `json:"reason,omitempty"`
	AuditSeq int       `json:"audit_seq"`
}

var (
	ErrDenied       = errors.New("agentgate: action denied by policy")
	ErrVerifyFailed = errors.New("agentgate: proposal failed verification")
)

// Execute runs the pipeline for one agent action:
//
//	1. Policy  — is (actor, action) listed? deny-by-default otherwise.
//	2. Propose — the untrusted agent computes a proposal.
//	3. Verify  — the trusted verifier judges the proposal.
//	4. Commit  — commit() runs ONLY on allow; RequireApproval returns
//	             Pending and commit() is deferred to Approve().
//
// Every outcome — including denials — is hash-chained into the audit
// trail. A denial you can't audit is a control you can't prove.
func (g *Gate) Execute(actor, action string, input any,
	propose func(input any) (any, error),
	commit func(proposal any) (any, error)) (Decision, error) {

	rule, listed := g.policy.Actions[action]
	if !listed {
		return g.deny(actor, action, "action not in policy (deny-by-default)")
	}
	allowed := false
	for _, a := range rule.Actors {
		if a == actor {
			allowed = true
			break
		}
	}
	if !allowed {
		return g.deny(actor, action, fmt.Sprintf("actor %q not allowed for %q", actor, action))
	}

	proposal, err := propose(input)
	if err != nil {
		return g.deny(actor, action, "propose() failed: "+err.Error())
	}

	verdict := Verdict{OK: proposal != nil}
	if g.verify != nil {
		verdict = g.verify(action, input, proposal)
	}
	if !verdict.OK {
		return g.reject(actor, action, verdict.Reason)
	}
	if rule.MaxRisk > 0 && verdict.Risk > rule.MaxRisk {
		return g.reject(actor, action,
			[]string{fmt.Sprintf("risk %.2f exceeds policy ceiling %.2f", verdict.Risk, rule.MaxRisk)})
	}

	if rule.RequireApproval {
		d := g.record(actor, action, "PEND_APPROVAL", "awaiting human commit")
		return Decision{Pending: true, Output: proposal, Reason: "human approval required", AuditSeq: d}, nil
	}

	out, err := commit(proposal)
	if err != nil {
		return g.deny(actor, action, "commit() failed: "+err.Error())
	}
	seq := g.record(actor, action, "ALLOW", "")
	return Decision{Allowed: true, Output: out, AuditSeq: seq}, nil
}

func (g *Gate) deny(actor, action, why string) (Decision, error) {
	seq := g.record(actor, action, "DENY", why)
	return Decision{Reason: why, AuditSeq: seq}, fmt.Errorf("%w: %s", ErrDenied, why)
}

func (g *Gate) reject(actor, action string, reasons []string) (Decision, error) {
	why := fmt.Sprintf("%v", reasons)
	seq := g.record(actor, action, "REJECT_VERIFY", why)
	return Decision{Reason: why, AuditSeq: seq}, ErrVerifyFailed
}

func (g *Gate) record(actor, action, decision, reason string) int {
	g.mu.Lock()
	defer g.mu.Unlock()
	prev := genesis
	if n := len(g.chain); n > 0 {
		prev = g.chain[n-1].Hash
	}
	e := AuditEntry{
		Seq: len(g.chain) + 1, TS: g.now().Format(time.RFC3339),
		Actor: actor, Action: action, Decision: decision,
		Reason: reason, PrevHash: prev,
	}
	e.Hash = hashEntry(e)
	g.chain = append(g.chain, e)
	if g.sink != nil {
		g.sink(e) // e.g. auditlake append — durable, off-box
	}
	return e.Seq
}

// Chain returns a copy of the in-memory audit trail (verify with
// packages/auditlake semantics: prev-hash links + content hashes).
func (g *Gate) Chain() []AuditEntry {
	g.mu.Lock()
	defer g.mu.Unlock()
	return append([]AuditEntry(nil), g.chain...)
}

// VerifyChain checks the in-memory trail (tamper check for the runtime;
// the durable guarantee is the sink → auditlake anchor).
func VerifyChain(chain []AuditEntry) error {
	prev := genesis
	for i, e := range chain {
		if e.Seq != i+1 {
			return fmt.Errorf("seq %d: gap or reorder", e.Seq)
		}
		if e.PrevHash != prev {
			return fmt.Errorf("seq %d: prev-hash link broken", e.Seq)
		}
		if e.Hash != hashEntry(e) {
			return fmt.Errorf("seq %d: content hash mismatch (tampered)", e.Seq)
		}
		prev = e.Hash
	}
	return nil
}
