package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

// Adjudicator evaluates a claim and returns a decision.
// Production seam: the Python rules engine (packages/adjudicator).
type Adjudicator interface {
	Adjudicate(c Claim) (Decision, error)
}

// Ledger posts double-entry money movements in minor units.
// Production seam: TigerBeetle behind the same primitives (ADR-0003);
// default implementation is the Rust seam binary (services/ledger).
type Ledger interface {
	// Post moves amount minor units from debit to credit under transaction id tx.
	Post(debit, credit, tx string, amount int) (string, error)
}

// Switch settles an approved claim with a Mojaloop-pattern FSP switch.
type Switch interface {
	Settle(claimID, payee string, amount int) (map[string]any, error)
}

// SubprocessAdjudicator shells out to the Python adjudicator (claim JSON on
// stdin, decision JSON on stdout). The seam is stable; a service-mesh variant
// can replace it without touching the handler.
type SubprocessAdjudicator struct {
	Python string // interpreter, e.g. "python3"
	Script string // path to adjudicate.py
}

func (s SubprocessAdjudicator) Adjudicate(c Claim) (Decision, error) {
	var d Decision
	body, _ := json.Marshal(c)
	cmd := exec.Command(s.Python, s.Script)
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return d, fmt.Errorf("adjudicator: %v: %s", err, out)
	}
	if err := json.Unmarshal(out, &d); err != nil {
		return d, fmt.Errorf("adjudicator decode: %v", err)
	}
	return d, nil
}

// SubprocessLedger drives the Rust ledger seam over stdin/stdout JSON lines.
type SubprocessLedger struct {
	Binary string // path to oh_ledger binary
}

func (l SubprocessLedger) Post(debit, credit, tx string, amount int) (string, error) {
	cmd := exec.Command(l.Binary)
	cmd.Stdin = strings.NewReader(fmt.Sprintf(
		`{"op":"post","debit":%q,"credit":%q,"amount":%d,"tx":%q}`+"\n"+`{"op":"balance","id":%q}`+"\n",
		debit, credit, amount, tx, credit))
	out, err := cmd.CombinedOutput()
	return string(out), err
}
