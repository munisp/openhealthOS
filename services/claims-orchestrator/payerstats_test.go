package main

// Payer stats tests: scheme mapping, decision mix, unmapped visibility.

import (
	"testing"
)

func TestPayerTallyMapping(t *testing.T) {
	tally := NewPayerTally()
	tally.Record(Decision{ClaimID: "C1", Decision: "APPROVE", Scheme: "BHCPF-VUL", PayableNGN: 2880})
	tally.Record(Decision{ClaimID: "C2", Decision: "APPROVE", Scheme: "BHCPF-VUL", PayableNGN: 1200})
	tally.Record(Decision{ClaimID: "C3", Decision: "PEND", Scheme: "HYGEIA-COMM"})
	tally.Record(Decision{ClaimID: "C4", Decision: "DENY", Scheme: "STATE-BASIC"})
	tally.Record(Decision{ClaimID: "C5", Decision: "APPROVE", Scheme: "NOPE-SCHEME", PayableNGN: 500})

	stats := tally.Stats()
	if len(stats) != 3 {
		t.Fatalf("want 3 payers, got %+v", stats)
	}
	byPayer := map[string]PayerStat{}
	for _, st := range stats {
		byPayer[st.Payer] = st
	}
	nhia := byPayer["NHIA"]
	if nhia.Claims != 3 || nhia.Approved != 2 || nhia.Denied != 1 || nhia.PayableNGN != 4080 || nhia.Kind != "government" {
		t.Fatalf("NHIA tally: %+v", nhia)
	}
	hy := byPayer["HYGEIA"]
	if hy.Claims != 1 || hy.Pended != 1 || hy.Kind != "commercial" {
		t.Fatalf("HYGEIA tally: %+v", hy)
	}
	un := byPayer["UNMAPPED"]
	if un.Claims != 1 || un.Kind != "unmapped" {
		t.Fatalf("unknown scheme must be visible as UNMAPPED: %+v", un)
	}
}

func TestPayerTallySorted(t *testing.T) {
	tally := NewPayerTally()
	tally.Record(Decision{Decision: "DENY", Scheme: "STATE-BASIC"})
	tally.Record(Decision{Decision: "DENY", Scheme: "HYGEIA-COMM"})
	stats := tally.Stats()
	if stats[0].Payer != "HYGEIA" || stats[1].Payer != "NHIA" {
		t.Fatalf("stats must be sorted: %+v", stats)
	}
}
