package agentgate

import (
	"errors"
	"strings"
	"testing"
)

func testPolicy() Policy {
	return Policy{Actions: map[string]Rule{
		"summarize_note":  {Actors: []string{"triage-llm"}, MaxRisk: 0.5},
		"prescribe":       {Actors: []string{"triage-llm"}, RequireApproval: true},
		"read_only_query": {Actors: []string{"triage-llm", "report-bot"}},
	}}
}

func okPropose(any) (any, error)  { return map[string]any{"summary": "ok"}, nil }
func okCommit(any) (any, error)   { return "committed", nil }
func okVerifier(string, any, any) Verdict {
	return Verdict{OK: true, Risk: 0.1}
}

func TestDenyByDefaultUnlistedAction(t *testing.T) {
	g := New(testPolicy(), okVerifier, nil)
	_, err := g.Execute("triage-llm", "delete_records", nil, okPropose, okCommit)
	if !errors.Is(err, ErrDenied) {
		t.Fatalf("unlisted action must deny: %v", err)
	}
	if got := g.Chain()[0].Decision; got != "DENY" {
		t.Fatalf("denial must be audited, got %s", got)
	}
}

func TestActorNotAllowed(t *testing.T) {
	g := New(testPolicy(), okVerifier, nil)
	_, err := g.Execute("rogue-bot", "read_only_query", nil, okPropose, okCommit)
	if !errors.Is(err, ErrDenied) || !strings.Contains(err.Error(), "rogue-bot") {
		t.Fatalf("wrong actor must deny: %v", err)
	}
}

func TestHappyPathCommitRunsOnce(t *testing.T) {
	g := New(testPolicy(), okVerifier, nil)
	commits := 0
	d, err := g.Execute("report-bot", "read_only_query", nil, okPropose,
		func(p any) (any, error) { commits++; return okCommit(p) })
	if err != nil || !d.Allowed || commits != 1 {
		t.Fatalf("want single commit: %+v err=%v commits=%d", d, err, commits)
	}
	if d.Output != "committed" {
		t.Fatalf("commit output must flow through: %v", d.Output)
	}
}

func TestVerifierRejectionBlocksCommit(t *testing.T) {
	g := New(testPolicy(), func(string, any, any) Verdict {
		return Verdict{OK: false, Reason: []string{"PII detected in output"}}
	}, nil)
	commits := 0
	_, err := g.Execute("triage-llm", "summarize_note", nil, okPropose,
		func(p any) (any, error) { commits++; return okCommit(p) })
	if !errors.Is(err, ErrVerifyFailed) || commits != 0 {
		t.Fatalf("failed verify must block commit: err=%v commits=%d", err, commits)
	}
}

func TestRiskCeilingBlocksCommit(t *testing.T) {
	g := New(testPolicy(), func(string, any, any) Verdict {
		return Verdict{OK: true, Risk: 0.9}
	}, nil)
	_, err := g.Execute("triage-llm", "summarize_note", nil, okPropose, okCommit)
	if !errors.Is(err, ErrVerifyFailed) {
		t.Fatalf("risk above ceiling must reject: %v", err)
	}
}

func TestRequireApprovalNeverCommits(t *testing.T) {
	g := New(testPolicy(), okVerifier, nil)
	commits := 0
	d, err := g.Execute("triage-llm", "prescribe", nil, okPropose,
		func(p any) (any, error) { commits++; return okCommit(p) })
	if err != nil || !d.Pending || d.Allowed || commits != 0 {
		t.Fatalf("RequireApproval must pend, never commit: %+v err=%v commits=%d", d, err, commits)
	}
	if got := g.Chain()[0].Decision; got != "PEND_APPROVAL" {
		t.Fatalf("pend must be audited, got %s", got)
	}
}

func TestProposeErrorDeniedAndAudited(t *testing.T) {
	g := New(testPolicy(), okVerifier, nil)
	_, err := g.Execute("triage-llm", "summarize_note", nil,
		func(any) (any, error) { return nil, errors.New("LLM timeout") }, okCommit)
	if !errors.Is(err, ErrDenied) {
		t.Fatalf("propose failure must deny: %v", err)
	}
}

func TestAuditChainTamperEvidence(t *testing.T) {
	g := New(testPolicy(), okVerifier, nil)
	g.Execute("report-bot", "read_only_query", nil, okPropose, okCommit)
	g.Execute("report-bot", "read_only_query", nil, okPropose, okCommit)
	chain := g.Chain()
	if err := VerifyChain(chain); err != nil {
		t.Fatalf("sound chain: %v", err)
	}
	chain[0].Actor = "mallory" // tamper
	if err := VerifyChain(chain); err == nil {
		t.Fatal("tampered chain must fail verification")
	}
}

func TestSinkReceivesEveryEntry(t *testing.T) {
	var sunk []AuditEntry
	g := New(testPolicy(), okVerifier, func(e AuditEntry) { sunk = append(sunk, e) })
	g.Execute("report-bot", "read_only_query", nil, okPropose, okCommit)
	g.Execute("mallory", "read_only_query", nil, okPropose, okCommit)
	if len(sunk) != 2 {
		t.Fatalf("sink must see allows AND denials, got %d", len(sunk))
	}
}
