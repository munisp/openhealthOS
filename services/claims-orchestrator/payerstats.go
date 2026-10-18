package main

// Payer stats (ADR-0012/0013): the orchestrator sees every adjudication,
// so it is the natural place to tally the payer-facing view — claims by
// payer, decision mix, and payable volume. This is what NHIA and the HMOs
// watch; payers who can see their money move pay faster.
//
// Scheme → payer mapping mirrors packages/adjudicator/payers.py defaults;
// production loads it from config (PAYER_SCHEMES="NHIA:BHCPF-VUL,STATE-BASIC;
// HYGEIA:HYGEIA-COMM"). Unknown schemes land in UNMAPPED — visible, never
// silently attributed.

import (
	"os"
	"sort"
	"strings"
	"sync"
)

// PayerStat is one payer's running totals.
type PayerStat struct {
	Payer      string `json:"payer"`
	Kind       string `json:"kind"` // government | commercial | unmapped
	Claims     int    `json:"claims"`
	Approved   int    `json:"approved"`
	Pended     int    `json:"pended"`
	Denied     int    `json:"denied"`
	PayableNGN int    `json:"payable_ngn"`
}

// PayerTally is the in-memory aggregate (per pod; the state-plane analytics
// pipeline is the durable cross-pod aggregate).
type PayerTally struct {
	mu      sync.Mutex
	byPayer map[string]*PayerStat
	schemes map[string]string // scheme -> payer id
	kinds   map[string]string // payer id -> kind
}

// NewPayerTally builds the scheme map from PAYER_SCHEMES or the defaults
// matching payers.py.
func NewPayerTally() *PayerTally {
	t := &PayerTally{byPayer: map[string]*PayerStat{}, schemes: map[string]string{}, kinds: map[string]string{}}
	spec := os.Getenv("PAYER_SCHEMES")
	if spec == "" {
		spec = "NHIA,government:BHCPF-VUL,STATE-BASIC,NHIA-FORMAL;HYGEIA,commercial:HYGEIA-COMM;RELIANCE,commercial:RELIANCE-CARE"
	}
	for _, payerSpec := range strings.Split(spec, ";") {
		head, schemes, ok := strings.Cut(payerSpec, ":")
		if !ok {
			continue
		}
		pid, kind, _ := strings.Cut(head, ",")
		if kind == "" {
			kind = "commercial"
		}
		t.kinds[pid] = kind
		for _, scheme := range strings.Split(schemes, ",") {
			t.schemes[strings.TrimSpace(scheme)] = pid
		}
	}
	return t
}

// Record folds one adjudication into the tally.
func (t *PayerTally) Record(d Decision) {
	payer, ok := t.schemes[d.Scheme]
	if !ok {
		payer = "UNMAPPED"
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	st := t.byPayer[payer]
	if st == nil {
		kind := t.kinds[payer]
		if kind == "" {
			kind = "unmapped"
		}
		st = &PayerStat{Payer: payer, Kind: kind}
		t.byPayer[payer] = st
	}
	st.Claims++
	switch d.Decision {
	case "APPROVE":
		st.Approved++
		st.PayableNGN += d.PayableNGN
	case "PEND":
		st.Pended++
	case "DENY":
		st.Denied++
	}
}

// Stats returns the tally sorted by payer id (stable for dashboards/tests).
func (t *PayerTally) Stats() []PayerStat {
	t.mu.Lock()
	defer t.mu.Unlock()
	out := make([]PayerStat, 0, len(t.byPayer))
	for _, st := range t.byPayer {
		out = append(out, *st)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Payer < out[j].Payer })
	return out
}
