// fraudhook.go — real-time pre-payment fraud gate (innovation 7).
//
// Fraud detection already exists as a batch/review package
// (packages/frauddetect, ADR-0014). This hook moves it INTO the money path:
// every APPROVE with a payable amount is scored before the ledger posts.
// A hot score flips the claim to PEND_REVIEW — a human reviews before money
// moves; the scorer NEVER denies (due process is a feature).
//
// Latency budget: the scorer runs subprocess (same pattern as the
// adjudicator) under a hard timeout. A slow or dead scorer fails OPEN to
// payment with an audit note — a claims outage is worse for patients than a
// missed review, and the batch sweep still catches the claim later.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	"time"
)

// FraudVerdict is the scorer's answer.
type FraudVerdict struct {
	Action  string   `json:"action"` // CLEAR | PEND
	Score   float64  `json:"score"`
	Reasons []string `json:"reasons"`
}

// FraudScorer is the seam (subprocess in production, fake in tests).
type FraudScorer interface {
	Score(claim map[string]any, history []map[string]any) (FraudVerdict, error)
}

// SubprocessFraudScorer shells to packages/frauddetect/frauddetect.py.
type SubprocessFraudScorer struct {
	Python  string
	Script  string
	Timeout time.Duration
}

func (s SubprocessFraudScorer) Score(claim map[string]any, history []map[string]any) (FraudVerdict, error) {
	body, _ := json.Marshal(map[string]any{"claim": claim, "history": history})
	timeout := s.Timeout
	if timeout == 0 {
		timeout = 2 * time.Second
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	python := s.Python
	if python == "" {
		python = "python3"
	}
	cmd := exec.CommandContext(ctx, python, s.Script)
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.Output()
	if err != nil {
		return FraudVerdict{}, err
	}
	var v FraudVerdict
	if err := json.Unmarshal(out, &v); err != nil {
		return FraudVerdict{}, err
	}
	return v, nil
}

// fraudScorerFromEnv wires the scorer; FRAUD_HOOK=off disables the gate
// (edge deployments without python). Default: on, subprocess to the
// bundled package.
func fraudScorerFromEnv(env func(string, string) string) FraudScorer {
	if env("FRAUD_HOOK", "") == "off" {
		return nil
	}
	return SubprocessFraudScorer{
		Python: env("PYTHON", ""),
		Script: resolveToolPath(env("FRAUD_DETECTOR", "../../packages/frauddetect/frauddetect.py")),
	}
}

// fraudHoldThreshold: at/above this score the claim pends for review.
// Env-tunable; default 0.7 (high precision — review queues cost nurses).
func fraudHoldThreshold(env func(string, string) string) float64 {
	if v := env("FRAUD_HOLD_THRESHOLD", ""); v != "" {
		var f float64
		if _, err := fmt.Sscanf(v, "%f", &f); err == nil && f > 0 && f <= 1 {
			return f
		}
	}
	return 0.7
}

// historyForScoring folds the decision store into the scorer's history
// shape (same-facility prior claims — the velocity/spike detectors'
// input).
func historyForScoring(store *DecisionStore, providerID string) []map[string]any {
	if store == nil {
		return nil
	}
	var out []map[string]any
	for _, d := range store.ForFacility(providerID) {
		out = append(out, map[string]any{
			"claim_id":    d.Claim.ClaimID,
			"provider_id": d.Claim.ProviderID,
			"member_id":   d.Claim.MemberID,
			"amount_ngn":  d.Decision.PayableNGN,
			"tariff_code":     firstCode(d.Claim),
			"ts":          d.At,
		})
	}
	return out
}

// firstCode: the scorer's upcoding baseline keys on the dominant tariff.
func firstCode(c Claim) string {
	if len(c.Items) > 0 {
		return c.Items[0].Code
	}
	return ""
}
