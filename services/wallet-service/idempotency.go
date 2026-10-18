package main

// Idempotency for money movement (ADR-0013). Networks fail after the
// money moves: a premium collection or claim payout that is retried by a
// client must never post twice. The defence has two layers:
//
//  1. Deterministic ledger tx ids (kind + "-" + ref) executed through the
//     ledger's two-phase prepare/commit — the ledger itself refuses a
//     txid it has already prepared (ADR-0007 journal makes that survive
//     restarts).
//  2. A wallet-side idempotency record (ref → posted tx) so a retry is
//     answered from the record without touching the ledger at all.
//
// Crash between ledger commit and record save: the retry re-issues the
// same deterministic txid, the ledger reports it as a duplicate, and the
// record is saved on the second pass. Money moves exactly once.

import (
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
)

// IdemStore persists ref → posted-tx records.
type IdemStore interface {
	Get(ref string) (tx string, ok bool)
	Put(ref, tx string) error
}

// MemoryIdemStore is the edge/dev default.
type MemoryIdemStore struct {
	mu sync.Mutex
	m  map[string]string
}

func NewMemoryIdemStore() *MemoryIdemStore {
	return &MemoryIdemStore{m: map[string]string{}}
}

func (s *MemoryIdemStore) Get(ref string) (string, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	tx, ok := s.m[ref]
	return tx, ok
}

func (s *MemoryIdemStore) Put(ref, tx string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.m[ref]; ok {
		return fmt.Errorf("idem record exists: %s", ref)
	}
	s.m[ref] = tx
	return nil
}

var ErrRefRequired = errors.New("ref required — money movement must be idempotent")

// CollectPremiumRef is CollectPremium with an idempotency key.
func (w *WalletService) CollectPremiumRef(memberID string, amount int, ref string) (tx string, duplicate bool, err error) {
	if ref == "" {
		tx, err := w.CollectPremium(memberID, amount)
		return tx, false, err // legacy path — callers without refs (tests, demos)
	}
	if w.refs != nil {
		if prev, ok := w.refs.Get(ref); ok {
			return prev, true, nil
		}
	}
	txid := "PREM-" + ref
	if err := validTxID(txid); err != nil {
		return "", false, err
	}
	// The two-phase post covers cash-in + pool transfer as one logical op:
	// prepare once, commit once — the ledger dedupes the txid.
	res, dup, err := w.postPremiumOnce(memberID, amount, txid)
	if err != nil {
		return "", false, err
	}
	if w.refs != nil && !dup {
		_ = w.refs.Put(ref, res) // a save failure is safe: ledger dedupes the retry
	}
	return res, dup, nil
}

// PayoutRef is Payout with a mandatory idempotency key.
func (w *WalletService) PayoutRef(facilityID string, amount int, ref string) (tx string, duplicate bool, err error) {
	if ref == "" {
		return "", false, ErrRefRequired
	}
	if amount <= 0 {
		return "", false, ErrBadAmount
	}
	if w.refs != nil {
		if prev, ok := w.refs.Get(ref); ok {
			return prev, true, nil
		}
	}
	txid := "PAYOUT-" + ref
	if err := validTxID(txid); err != nil {
		return "", false, err
	}
	res, dup, err := w.Ledger.PostOnce(acctPool, prefixWallet+facilityID, txid, amount)
	if err != nil {
		return "", false, err
	}
	if w.refs != nil && !dup {
		_ = w.refs.Put(ref, res)
	}
	return res, dup, nil
}

// postPremiumOnce runs the premium double-posting (cash-in + pool) with
// deterministic txids derived from the ref.
func (w *WalletService) postPremiumOnce(memberID string, amount int, txid string) (string, bool, error) {
	if amount <= 0 {
		return "", false, ErrBadAmount
	}
	if memberID == "" {
		return "", false, errors.New("member id required")
	}
	acct := prefixMember + memberID
	if _, err := w.Ledger.Open(acct, amount); err != nil {
		if _, _, err2 := w.Ledger.PostOnce("PSP-FLOAT", acct, txid+"-CASHIN", amount); err2 != nil {
			return "", false, err2
		}
	}
	res, dup, err := w.Ledger.PostOnce(acct, acctPool, txid, amount)
	if err != nil {
		return "", false, err
	}
	return res, dup, nil
}

// validTxID keeps tx ids parseable and journal-safe.
func validTxID(txid string) error {
	if len(txid) > 128 || strings.ContainsAny(txid, "\"\n\r\t ") {
		return fmt.Errorf("bad tx id: %q", txid)
	}
	return nil
}

// _ keeps time imported when build tags shift during development.
var _ = time.Now
