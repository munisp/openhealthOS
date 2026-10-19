package main

// Citizen health wallets (ADR-0030): government-funded, purpose-restricted
// money that lives on the patient's phone — and can only ever pay for
// healthcare.
//
// Doctrine: the restriction is an ALLOW-LIST, never a deny-list (same
// doctrine as the de-ID export keep-list in ADR-0028). A citizen wallet
// can pay only destinations on the health allow-list — facility wallets,
// pharmacies, ambulance dispatch, telehealth — and every refusal is a
// refused posting, so the audit trail shows attempts, not just successes.
//
// Sub-programme envelopes (`CITIZEN-<nin>:MATERNAL`, `:UNDER5`, …) keep a
// maternal-health grant from leaking into general spend; envelopes can
// expire, at which point unspent funds sweep back to the programme
// account. All movements are PostOnce-idempotent double-entry postings on
// the ledger seam — the wallet never hand-rolls a balance.

import (
	"errors"
	"fmt"
	"strings"
	"sync"
	"time"
)

// Account prefixes on the chart (ADR-0003).
const (
	prefixCitizen = "CITIZEN-"       // citizen health wallet: CITIZEN-<nin>
	prefixGovProg = "GOV-PROGRAMME-" // treasury-funded subsidy source
	treasuryFloat = "TREASURY-FLOAT" // settlement float for programme top-ups
)

// healthDestinations is the allow-list of payable prefixes. Anything not
// here — airtime, transfers to friends, betting — is refused at the
// service layer before the ledger is ever asked.
var healthDestinations = map[string]string{
	"WALLET-":  "facility",
	"PHARM-":   "pharmacy",
	"NEMTC-":   "ambulance",
	"TELEMED-": "telehealth",
}

// EnvelopePolicy narrows the allow-list further per sub-programme.
type EnvelopePolicy struct {
	Name        string
	Destination []string // allowed destination prefixes
}

var envelopePolicies = map[string]EnvelopePolicy{
	"GENERAL":   {"GENERAL", []string{"WALLET-", "PHARM-", "NEMTC-", "TELEMED-"}},
	"MATERNAL":  {"MATERNAL", []string{"WALLET-"}},
	"UNDER5":    {"UNDER5", []string{"WALLET-", "PHARM-"}},
	"EMERGENCY": {"EMERGENCY", []string{"WALLET-", "NEMTC-"}},
	"NCD":       {"NCD", []string{"WALLET-", "PHARM-", "TELEMED-"}},
}

var (
	ErrRestrictedDestination = errors.New("destination is not on the healthcare allow-list — citizen wallet funds can only pay for care")
	ErrUnknownEnvelope       = errors.New("unknown sub-programme envelope")
	ErrEnvelopeExpired       = errors.New("envelope expired — unspent funds return to the programme")
	ErrBadNIN                = errors.New("nin required")
)

// EnvelopeRecord is the service-level metadata the ledger cannot hold:
// which programme funded an envelope and when it expires. Balances stay
// on the ledger; policy lives here.
type EnvelopeRecord struct {
	Programme string    `json:"programme"`
	ExpiresAt time.Time `json:"expires_at"`
}

func citizenAcct(nin, envelope string) string {
	if envelope == "" || envelope == "GENERAL" {
		return prefixCitizen + nin
	}
	return prefixCitizen + nin + ":" + envelope
}

// citizenEnv is the registry of funded envelopes (pilot: in-memory;
// state plane: Postgres, same table shape).
type citizenEnv struct {
	mu      sync.Mutex
	records map[string]EnvelopeRecord // citizenAcct → record
}

func newCitizenEnv() *citizenEnv { return &citizenEnv{records: map[string]EnvelopeRecord{}} }

// Funder types. Government disbursements draw a programme account
// (treasury-settled); private gifts (a diaspora relative, a friend, an
// employer) settle through the PSP/remittance float — same seam as
// premium cash-in. Either way the funds land in the same allow-listed
// citizen wallet: a gift of health money cannot be diverted to anything
// but care.
const (
	FunderGovernment = "GOVERNMENT"
	FunderPrivate    = "PRIVATE"
)

// FundCitizen books a government subsidy into a citizen's health wallet
// (or a named envelope). ref is mandatory — subsidy disbursement is
// exactly-once money movement.
func (w *WalletService) FundCitizen(nin, envelope string, amount int, programme, ref string, expiresInDays int) (tx string, duplicate bool, err error) {
	return w.fundFrom(nin, envelope, amount, FunderGovernment, programme, ref, expiresInDays)
}

// GiftCitizen books a private gift into a citizen's health wallet. The
// giver pays by card/bank/remittance; the PSP settles naira into the
// float and the wallet is credited — the giver never needs a ledger
// account. giver is recorded in the tx id for the audit trail.
func (w *WalletService) GiftCitizen(nin, envelope string, amount int, giver, ref string, expiresInDays int) (tx string, duplicate bool, err error) {
	if giver == "" {
		return "", false, errors.New("giver required — gifts are attributed")
	}
	return w.fundFrom(nin, envelope, amount, FunderPrivate, giver, ref, expiresInDays)
}

// fundFrom is the shared funding path: source selection by funder type,
// then an exactly-once posting into the citizen wallet.
func (w *WalletService) fundFrom(nin, envelope string, amount int, funderType, source, ref string, expiresInDays int) (tx string, duplicate bool, err error) {
	if nin == "" {
		return "", false, ErrBadNIN
	}
	if envelope == "" {
		envelope = "GENERAL"
	}
	if _, ok := envelopePolicies[envelope]; !ok {
		return "", false, ErrUnknownEnvelope
	}
	if amount <= 0 {
		return "", false, ErrBadAmount
	}
	if ref == "" {
		return "", false, ErrRefRequired
	}
	if source == "" {
		return "", false, errors.New("funding source required")
	}
	if w.refs != nil {
		if prev, ok := w.refs.Get(ref); ok {
			return prev, true, nil
		}
	}
	var src, txPrefix, floatAcct string
	switch funderType {
	case FunderGovernment:
		src = prefixGovProg + source
		txPrefix = "FUND-"
		floatAcct = treasuryFloat
	case FunderPrivate:
		src = "GIFTS-IN" // PSP/remittance settlement account
		txPrefix = "GIFT-" + source + "-"
		floatAcct = "PSP-FLOAT" // same float as premium cash-in
	default:
		return "", false, fmt.Errorf("unknown funder type %q", funderType)
	}
	dst := citizenAcct(nin, envelope)
	if _, err := w.Ledger.Open(src, 0); err != nil {
		// Source exists — funding is a posting, not an opening.
	}
	if _, _, err := w.Ledger.PostOnce(floatAcct, src, "SETTLE-"+ref, amount); err != nil {
		return "", false, err
	}
	if _, err := w.Ledger.Open(dst, 0); err != nil {
		// Wallet exists — fine.
	}
	txid := txPrefix + ref
	if err := validTxID(txid); err != nil {
		return "", false, err
	}
	res, dup, err := w.Ledger.PostOnce(src, dst, txid, amount)
	if err != nil {
		return "", false, err
	}
	if !dup {
		rec := EnvelopeRecord{Programme: funderType + ":" + source}
		if expiresInDays > 0 {
			rec.ExpiresAt = time.Now().UTC().Add(time.Duration(expiresInDays) * 24 * time.Hour)
		}
		w.envReg().set(dst, rec)
		if w.refs != nil {
			_ = w.refs.Put(ref, res)
		}
	}
	return res, dup, nil
}

// CitizenPay moves funds from a citizen wallet to a healthcare
// destination. The allow-list is enforced BEFORE the ledger is touched;
// refusals return ErrRestrictedDestination.
func (w *WalletService) CitizenPay(nin, envelope, destination string, amount int, ref string) (tx string, duplicate bool, err error) {
	if nin == "" {
		return "", false, ErrBadNIN
	}
	if envelope == "" {
		envelope = "GENERAL"
	}
	pol, ok := envelopePolicies[envelope]
	if !ok {
		return "", false, ErrUnknownEnvelope
	}
	if amount <= 0 {
		return "", false, ErrBadAmount
	}
	if ref == "" {
		return "", false, ErrRefRequired
	}
	if !w.destAllowed(pol, destination) {
		return "", false, fmt.Errorf("%w: %q", ErrRestrictedDestination, destination)
	}
	src := citizenAcct(nin, envelope)
	if rec, ok := w.envReg().get(src); ok && !rec.ExpiresAt.IsZero() && time.Now().UTC().After(rec.ExpiresAt) {
		return "", false, ErrEnvelopeExpired
	}
	if w.refs != nil {
		if prev, ok := w.refs.Get(ref); ok {
			return prev, true, nil
		}
	}
	// The destination is allow-listed; opening its wallet on first payment
	// is safe (the prefix is the policy, not pre-registration).
	if _, err := w.Ledger.Open(destination, 0); err != nil {
		// exists — fine
	}
	txid := "CPAY-" + ref
	if err := validTxID(txid); err != nil {
		return "", false, err
	}
	res, dup, err := w.Ledger.PostOnce(src, destination, txid, amount)
	if err != nil {
		return "", false, err
	}
	if w.refs != nil && !dup {
		_ = w.refs.Put(ref, res)
	}
	return res, dup, nil
}

func (w *WalletService) destAllowed(pol EnvelopePolicy, destination string) bool {
	for _, prefix := range pol.Destination {
		if strings.HasPrefix(destination, prefix) {
			return true
		}
	}
	return false
}

// CitizenBalances reports total and per-envelope balances in kobo.
func (w *WalletService) CitizenBalances(nin string) (map[string]int, int, error) {
	if nin == "" {
		return nil, 0, ErrBadNIN
	}
	out := map[string]int{}
	total := 0
	for name := range envelopePolicies {
		bal, err := w.Ledger.Balance(citizenAcct(nin, name))
		if err != nil {
			continue // envelope never funded → not an account
		}
		if bal != 0 {
			out[name] = bal
			total += bal
		}
	}
	return out, total, nil
}

// SweepExpired returns an expired envelope's unspent funds to its funding
// programme. Actor is required (treasury operation, audit doctrine).
func (w *WalletService) SweepExpired(nin, envelope, actor string) (string, error) {
	if actor == "" {
		return "", errors.New("actor required")
	}
	if nin == "" {
		return "", ErrBadNIN
	}
	acct := citizenAcct(nin, envelope)
	rec, ok := w.envReg().get(acct)
	if !ok {
		return "", fmt.Errorf("no envelope record for %s", acct)
	}
	if rec.ExpiresAt.IsZero() || time.Now().UTC().Before(rec.ExpiresAt) {
		return "", errors.New("envelope has not expired")
	}
	bal, err := w.Ledger.Balance(acct)
	if err != nil || bal <= 0 {
		return "OK: nothing to sweep", nil
	}
	// Funds return to their origin: programme account for government
	// money, the gift settlement account for private money.
	dest := "GIFTS-IN"
	if prog, ok := strings.CutPrefix(rec.Programme, FunderGovernment+":"); ok {
		dest = prefixGovProg + prog
	}
	res, _, err := w.Ledger.PostOnce(acct, dest,
		fmt.Sprintf("SWEEP-%s-%d", acct, time.Now().UTC().Unix()), bal)
	if err != nil {
		return "", err
	}
	return res, nil
}

func (e *citizenEnv) set(acct string, rec EnvelopeRecord) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.records[acct] = rec
}

func (e *citizenEnv) get(acct string) (EnvelopeRecord, bool) {
	e.mu.Lock()
	defer e.mu.Unlock()
	r, ok := e.records[acct]
	return r, ok
}

// Destinations exposes the allow-list for UI/documentation.
func Destinations() map[string]string {
	out := map[string]string{}
	for k, v := range healthDestinations {
		out[k] = v
	}
	return out
}
