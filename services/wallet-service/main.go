// Package main — OpenHealth OS wallet service (module 5, payments).
//
// Facility wallets and premium micro-collections (₦500–2,000 instalments —
// 93% of employment is informal, so premiums arrive in small, irregular,
// mobile payments). Every movement is a double-entry posting on the ledger
// seam (ADR-0003): balances are never hand-rolled here.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os/exec"
	"strings"
	"sync"
)

// Ledger is the double-entry seam (services/ledger; TigerBeetle in prod).
type Ledger interface {
	Open(id string, opening int) (string, error)
	Post(debit, credit, tx string, amount int) (string, error)
	// PostOnce posts through two-phase prepare/commit with a caller-chosen
	// deterministic txid: the ledger refuses to prepare the same txid
	// twice, so a retry after a crash can never double-post. duplicate is
	// true when the txid was already fully committed before this call.
	PostOnce(debit, credit, txid string, amount int) (result string, duplicate bool, err error)
	Balance(id string) (int, error)
}

// SubprocessLedger drives the Rust seam binary over JSON lines.
type SubprocessLedger struct{ Binary string }

func (l SubprocessLedger) cmd(ops ...string) ([]string, error) {
	c := exec.Command(l.Binary)
	c.Stdin = strings.NewReader(strings.Join(ops, "\n") + "\n")
	out, err := c.CombinedOutput()
	if err != nil {
		return nil, err
	}
	return strings.Split(strings.TrimSpace(string(out)), "\n"), nil
}

func (l SubprocessLedger) Open(id string, opening int) (string, error) {
	// skip the 3 boot lines printed by the seam binary
	lines, err := l.cmd(fmt.Sprintf(`{"op":"open","id":%q,"opening":%d}`, id, opening))
	if err != nil {
		return "", err
	}
	return lines[len(lines)-1], nil
}

func (l SubprocessLedger) Post(debit, credit, tx string, amount int) (string, error) {
	lines, err := l.cmd(fmt.Sprintf(
		`{"op":"post","debit":%q,"credit":%q,"amount":%d,"tx":%q}`, debit, credit, amount, tx))
	if err != nil {
		return "", err
	}
	return lines[len(lines)-1], nil
}

// PostOnce drives prepare+commit over the seam binary. The txid is the
// idempotency anchor: a repeated prepare errors "already prepared" (the
// crash window — commit completes it), and a commit with nothing prepared
// means the tx fully posted before (true duplicate).
func (l SubprocessLedger) PostOnce(debit, credit, txid string, amount int) (string, bool, error) {
	prep := fmt.Sprintf(`{"op":"prepare","txid":%q,"debit":%q,"credit":%q,"amount":%d}`, txid, debit, credit, amount)
	com := fmt.Sprintf(`{"op":"commit","txid":%q}`, txid)
	lines, err := l.cmd(prep, com)
	if err != nil {
		return "", false, err
	}
	if len(lines) < 2 {
		return "", false, fmt.Errorf("ledger: short reply: %v", lines)
	}
	prepLine, comLine := lines[len(lines)-2], lines[len(lines)-1]
	switch {
	case strings.HasPrefix(comLine, "OK:"):
		return comLine, false, nil // posted now (fresh or crash-window completion)
	case strings.Contains(comLine, "no prepared tx") && strings.Contains(prepLine, "already prepared"):
		return "duplicate of " + txid, true, nil // fully committed before
	default:
		return "", false, fmt.Errorf("ledger: %s | %s", prepLine, comLine)
	}
}

func (l SubprocessLedger) Balance(id string) (int, error) {
	lines, err := l.cmd(fmt.Sprintf(`{"op":"balance","id":%q}`, id))
	if err != nil {
		return 0, err
	}
	var bal int
	if _, err := fmt.Sscanf(lines[len(lines)-1], "OK: "+id+" = %d", &bal); err != nil {
		return 0, errors.New("balance: " + lines[len(lines)-1])
	}
	return bal, nil
}

// ---- wallet domain ----

// Account prefixes on the chart (ADR-0003).
const (
	acctPool     = "SETTLE-POOL"     // scheme settlement pool
	prefixWallet = "WALLET-"         // facility wallet
	prefixMember = "MEMBER-PREMIUM-" // member premium contributions
)

// WalletService exposes collections and payouts over the ledger seam.
type WalletService struct {
	Ledger Ledger
	// refs is the idempotency record store (ADR-0013); nil keeps the
	// legacy non-idempotent path for tests and demos.
	refs  IdemStore
	mu    sync.Mutex
	txSeq int
}

var ErrBadAmount = errors.New("amount must be positive minor units (kobo)")

func (w *WalletService) nextTx(kind string) string {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.txSeq++
	return fmt.Sprintf("%s-%06d", kind, w.txSeq)
}

// OpenWallet opens a facility wallet (zero balance).
func (w *WalletService) OpenWallet(facilityID string) (string, error) {
	if facilityID == "" {
		return "", errors.New("facility id required")
	}
	return w.Ledger.Open(prefixWallet+facilityID, 0)
}

// CollectPremium books one member premium instalment into the scheme pool.
// Micro-collections are first-class: ₦500–2,000 instalments are the norm,
// not the edge case.
func (w *WalletService) CollectPremium(memberID string, amount int) (string, error) {
	if amount <= 0 {
		return "", ErrBadAmount
	}
	if memberID == "" {
		return "", errors.New("member id required")
	}
	// Cash-in: the PSP settles the instalment, which funds the member's
	// premium account (opening credit) — then the account pays the pool.
	// Double-entry all the way down; no hand-rolled balances.
	acct := prefixMember + memberID
	if _, err := w.Ledger.Open(acct, amount); err != nil {
		// account exists (returning member): top-up posts from the PSP float
		if _, err2 := w.Ledger.Post("PSP-FLOAT", acct, w.nextTx("CASHIN"), amount); err2 != nil {
			return "", err2
		}
	}
	return w.Ledger.Post(acct, acctPool, w.nextTx("PREM"), amount)
}

// Payout pays a facility wallet from the settlement pool (e.g. after an
// approved claim settles — the claims orchestrator drives this).
func (w *WalletService) Payout(facilityID string, amount int, ref string) (string, error) {
	if amount <= 0 {
		return "", ErrBadAmount
	}
	return w.Ledger.Post(acctPool, prefixWallet+facilityID, w.nextTx("PAYOUT-"+ref), amount)
}

// Balance returns a facility wallet balance in kobo.
func (w *WalletService) Balance(facilityID string) (int, error) {
	return w.Ledger.Balance(prefixWallet + facilityID)
}

// ---- HTTP surface ----

type server struct{ w *WalletService }

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/wallets", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			FacilityID string `json:"facility_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		res, err := s.w.OpenWallet(req.FacilityID)
		if err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		writeJSON(w, 201, map[string]string{"wallet": prefixWallet + req.FacilityID, "ledger": res})
	})
	mux.HandleFunc("/premiums", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			MemberID   string `json:"member_id"`
			AmountKobo int    `json:"amount_kobo"`
			Ref        string `json:"ref"` // idempotency key (PSP receipt no.)
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		res, dup, err := s.w.CollectPremiumRef(req.MemberID, req.AmountKobo, req.Ref)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if dup {
			writeJSON(w, 200, map[string]any{"ledger": res, "duplicate": true})
			return
		}
		writeJSON(w, 201, map[string]string{"ledger": res})
	})
	mux.HandleFunc("/payouts", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			FacilityID string `json:"facility_id"`
			AmountKobo int    `json:"amount_kobo"`
			Ref        string `json:"ref"` // e.g. claim id — REQUIRED (idempotency)
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		res, dup, err := s.w.PayoutRef(req.FacilityID, req.AmountKobo, req.Ref)
		switch {
		case errors.Is(err, ErrRefRequired):
			http.Error(w, err.Error(), http.StatusBadRequest)
		case err != nil:
			http.Error(w, err.Error(), http.StatusBadRequest)
		case dup:
			writeJSON(w, 200, map[string]any{"ledger": res, "duplicate": true})
		default:
			writeJSON(w, 201, map[string]string{"ledger": res})
		}
	})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}
