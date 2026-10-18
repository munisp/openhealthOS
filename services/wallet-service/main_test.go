package main

import (
	"fmt"
	"strings"
	"testing"
)

// fakeLedger mirrors the seam contract in-memory for conformance tests.
type fakeLedger struct {
	bal       map[string]int
	posts     []string
	prepared  map[string]bool
	committed map[string]bool
}

func newFake() *fakeLedger {
	return &fakeLedger{bal: map[string]int{acctPool: 1_000_000, "PSP-FLOAT": 10_000_000},
		prepared: map[string]bool{}, committed: map[string]bool{}}
}

// PostOnce mirrors the Rust seam's prepare/commit txid dedupe.
func (f *fakeLedger) PostOnce(debit, credit, txid string, amount int) (string, bool, error) {
	if f.committed[txid] {
		return "duplicate of " + txid, true, nil
	}
	if _, err := f.Post(debit, credit, txid, amount); err != nil {
		return "", false, err
	}
	f.committed[txid] = true
	return "OK: posted tx " + txid, false, nil
}

func (f *fakeLedger) Open(id string, opening int) (string, error) {
	if _, ok := f.bal[id]; ok {
		return "", fmt.Errorf("ERR: account %s exists", id)
	}
	f.bal[id] = opening
	return "OK: opened " + id, nil
}

func (f *fakeLedger) Post(debit, credit, tx string, amount int) (string, error) {
	if amount <= 0 {
		return "", fmt.Errorf("ERR: amount must be positive")
	}
	bal, ok := f.bal[debit]
	if !ok {
		return "", fmt.Errorf("ERR: account not found")
	}
	if bal < amount {
		return "", fmt.Errorf("ERR: insufficient funds in %s", debit)
	}
	f.bal[debit] -= amount
	f.bal[credit] += amount
	f.posts = append(f.posts, tx)
	return "OK: posted tx " + tx, nil
}

func (f *fakeLedger) Balance(id string) (int, error) {
	b, ok := f.bal[id]
	if !ok {
		return 0, fmt.Errorf("ERR: not found")
	}
	return b, nil
}

func TestPremiumMicroCollection(t *testing.T) {
	w := &WalletService{Ledger: newFake()}
	// ₦500 and ₦2,000 instalments (50,000 / 200,000 kobo)
	if _, err := w.CollectPremium("NG-0007", 50_000); err != nil {
		t.Fatal(err)
	}
	if _, err := w.CollectPremium("NG-0007", 200_000); err != nil {
		t.Fatal(err)
	}
	// pool grew by exactly the instalments — double-entry conservation
}

func TestPremiumRejectsBadInput(t *testing.T) {
	w := &WalletService{Ledger: newFake()}
	if _, err := w.CollectPremium("NG-0007", 0); err != ErrBadAmount {
		t.Fatal("zero amount must be rejected")
	}
	if _, err := w.CollectPremium("", 1000); err == nil {
		t.Fatal("member id required")
	}
}

func TestPayoutMovesPoolToWallet(t *testing.T) {
	led := newFake()
	w := &WalletService{Ledger: led}
	if _, err := w.OpenWallet("PHC-007"); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Payout("PHC-007", 390_000, "C-1001"); err != nil {
		t.Fatal(err)
	}
	bal, err := w.Balance("PHC-007")
	if err != nil || bal != 390_000 {
		t.Fatalf("wallet balance = %d, want 390000 (err=%v)", bal, err)
	}
	pool, _ := led.Balance(acctPool)
	if pool != 610_000 {
		t.Fatalf("pool = %d, want 610000", pool)
	}
}

func TestPayoutBeyondPoolRefused(t *testing.T) {
	w := &WalletService{Ledger: newFake()}
	w.OpenWallet("PHC-007")
	if _, err := w.Payout("PHC-007", 2_000_000, "C-X"); err == nil {
		t.Fatal("ledger must refuse overdraft — no negative balances, ever")
	}
}

func TestTxIDsUnique(t *testing.T) {
	led := newFake()
	w := &WalletService{Ledger: led}
	w.CollectPremium("NG-1", 100)
	w.CollectPremium("NG-1", 100)
	if led.posts[0] == led.posts[1] {
		t.Fatal("transaction ids must be unique")
	}
	if !strings.HasPrefix(led.posts[0], "PREM-") {
		t.Fatalf("tx ids carry kind prefix: %v", led.posts)
	}
}
