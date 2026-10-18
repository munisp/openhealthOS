package main

// Idempotency tests (ADR-0013): money moves exactly once under retries,
// and the record store answers replays without touching the ledger.

import (
	"errors"
	"strings"
	"testing"
)

func TestPayoutRetryNeverDoublePosts(t *testing.T) {
	led := newFake()
	w := &WalletService{Ledger: led, refs: NewMemoryIdemStore()}
	poolBefore, _ := led.Balance(acctPool)

	tx1, dup1, err := w.PayoutRef("PHC-1", 288_000, "CLM-2880")
	if err != nil || dup1 {
		t.Fatalf("first payout: %v dup=%v", err, dup1)
	}
	// Client times out, retries the same ref.
	tx2, dup2, err := w.PayoutRef("PHC-1", 288_000, "CLM-2880")
	if err != nil || !dup2 {
		t.Fatalf("retry must be a duplicate: %v dup=%v", err, dup2)
	}
	if tx1 != tx2 {
		t.Fatalf("replay must return the original tx: %q vs %q", tx1, tx2)
	}
	poolAfter, _ := led.Balance(acctPool)
	if poolBefore-poolAfter != 288_000 {
		t.Fatalf("pool moved %d — money moved twice", poolBefore-poolAfter)
	}
}

func TestPayoutRequiresRef(t *testing.T) {
	w := &WalletService{Ledger: newFake(), refs: NewMemoryIdemStore()}
	if _, _, err := w.PayoutRef("PHC-1", 100, ""); !errors.Is(err, ErrRefRequired) {
		t.Fatalf("refless payout must be refused: %v", err)
	}
}

func TestPremiumIdempotencyKey(t *testing.T) {
	led := newFake()
	w := &WalletService{Ledger: led, refs: NewMemoryIdemStore()}
	if _, dup, err := w.CollectPremiumRef("NG-0007", 50_000, "PSP-RCPT-1"); err != nil || dup {
		t.Fatalf("first: %v %v", err, dup)
	}
	poolAfterFirst, _ := led.Balance(acctPool)
	_, dup, err := w.CollectPremiumRef("NG-0007", 50_000, "PSP-RCPT-1")
	if err != nil || !dup {
		t.Fatalf("retry: %v dup=%v", err, dup)
	}
	bal, _ := led.Balance(acctPool)
	if bal != poolAfterFirst {
		t.Fatalf("premium posted twice: pool moved %d -> %d on retry", poolAfterFirst, bal)
	}
}

func TestLegacyReflessPremiumStillWorks(t *testing.T) {
	// Tests, demos, and offline edge flows without PSP refs keep the
	// legacy sequence-based path — idempotency is mandatory only where a
	// ref exists to key on (payouts) or is provided (premiums).
	w := &WalletService{Ledger: newFake(), refs: NewMemoryIdemStore()}
	if _, dup, err := w.CollectPremiumRef("NG-0008", 50_000, ""); err != nil || dup {
		t.Fatalf("legacy premium: %v dup=%v", err, dup)
	}
}

func TestIdemStoreRejectsDoublePut(t *testing.T) {
	s := NewMemoryIdemStore()
	if err := s.Put("R1", "TX-1"); err != nil {
		t.Fatal(err)
	}
	if err := s.Put("R1", "TX-2"); err == nil {
		t.Fatal("second Put for the same ref must fail — silent overwrite hides fraud")
	}
}

func TestTxIDHygiene(t *testing.T) {
	w := &WalletService{Ledger: newFake(), refs: NewMemoryIdemStore()}
	if _, _, err := w.PayoutRef("PHC-1", 100, `bad "ref`); err == nil ||
		!strings.Contains(err.Error(), "bad tx id") {
		t.Fatalf("hostile ref must be rejected before touching the ledger: %v", err)
	}
}
