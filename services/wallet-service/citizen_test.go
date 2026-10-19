package main

import (
	"errors"
	"testing"
)

func newCitizenWallet() (*WalletService, *fakeLedger) {
	f := newFake()
	f.bal[treasuryFloat] = 100_000_000
	return &WalletService{Ledger: f, refs: NewMemoryIdemStore()}, f
}

func TestFundCitizenOpensWalletAndEnvelope(t *testing.T) {
	w, f := newCitizenWallet()
	tx, dup, err := w.FundCitizen("NIN-1", "GENERAL", 5000_00, "BHCPF", "batch-001", 0)
	if err != nil || dup {
		t.Fatalf("fund: %v dup=%v", err, dup)
	}
	if tx == "" {
		t.Fatal("expected ledger tx")
	}
	if f.bal["CITIZEN-NIN-1"] != 5000_00 {
		t.Fatalf("citizen balance = %d", f.bal["CITIZEN-NIN-1"])
	}
	// Programme account was funded from the treasury float on open.
	if f.bal[prefixGovProg+"BHCPF"] != 0 {
		t.Fatalf("programme should be fully disbursed, got %d", f.bal[prefixGovProg+"BHCPF"])
	}

	// Second funding to the same wallet (envelope this time).
	if _, _, err := w.FundCitizen("NIN-1", "MATERNAL", 10000_00, "BHCPF-M", "batch-002", 90); err != nil {
		t.Fatal(err)
	}
	if f.bal["CITIZEN-NIN-1:MATERNAL"] != 10000_00 {
		t.Fatalf("maternal envelope = %d", f.bal["CITIZEN-NIN-1:MATERNAL"])
	}

	envs, total, err := w.CitizenBalances("NIN-1")
	if err != nil {
		t.Fatal(err)
	}
	if total != 15000_00 || envs["GENERAL"] != 5000_00 || envs["MATERNAL"] != 10000_00 {
		t.Fatalf("balances: %v total=%d", envs, total)
	}
}

func TestFundingIsExactlyOnce(t *testing.T) {
	w, f := newCitizenWallet()
	w.FundCitizen("NIN-2", "", 2000_00, "BHCPF", "ref-x", 0)
	_, dup, err := w.FundCitizen("NIN-2", "", 2000_00, "BHCPF", "ref-x", 0)
	if err != nil || !dup {
		t.Fatalf("retry must be answered as duplicate: %v dup=%v", err, dup)
	}
	if f.bal["CITIZEN-NIN-2"] != 2000_00 {
		t.Fatalf("double funding! balance = %d", f.bal["CITIZEN-NIN-2"])
	}
}

func TestCitizenPayAllowList(t *testing.T) {
	w, f := newCitizenWallet()
	w.FundCitizen("NIN-3", "GENERAL", 5000_00, "BHCPF", "b1", 0)

	// Allowed: facility wallet.
	if _, _, err := w.CitizenPay("NIN-3", "GENERAL", "WALLET-phc-karu", 1500_00, "pay-1"); err != nil {
		t.Fatalf("facility payment must succeed: %v", err)
	}
	if f.bal["WALLET-phc-karu"] != 1500_00 {
		t.Fatalf("facility credited %d", f.bal["WALLET-phc-karu"])
	}

	// Refused: airtime, betting, arbitrary person.
	for _, dest := range []string{"AIRTIME-MTN", "BET-NAIJA", "CITIZEN-NIN-9", "FRIEND-1"} {
		if _, _, err := w.CitizenPay("NIN-3", "GENERAL", dest, 100_00, "pay-x-"+dest); !errors.Is(err, ErrRestrictedDestination) {
			t.Fatalf("destination %q must be refused, got %v", dest, err)
		}
	}
	// Refusals never moved money.
	if f.bal["CITIZEN-NIN-3"] != 3500_00 {
		t.Fatalf("balance after refusals = %d, want 350000", f.bal["CITIZEN-NIN-3"])
	}
}

func TestEnvelopePolicyNarrowsAllowList(t *testing.T) {
	w, _ := newCitizenWallet()
	w.FundCitizen("NIN-4", "MATERNAL", 10000_00, "BHCPF-M", "b2", 0)

	// Maternal funds pay a facility…
	if _, _, err := w.CitizenPay("NIN-4", "MATERNAL", "WALLET-fmc-jabi", 4000_00, "m1"); err != nil {
		t.Fatalf("maternal → facility: %v", err)
	}
	// …but not a pharmacy or telehealth (policy narrows the allow-list).
	if _, _, err := w.CitizenPay("NIN-4", "MATERNAL", "PHARM-medplus", 500_00, "m2"); !errors.Is(err, ErrRestrictedDestination) {
		t.Fatalf("maternal → pharmacy must be refused: %v", err)
	}
	// Under-5 envelope does allow pharmacies.
	w.FundCitizen("NIN-4", "UNDER5", 3000_00, "BHCPF-U5", "b3", 0)
	if _, _, err := w.CitizenPay("NIN-4", "UNDER5", "PHARM-medplus", 500_00, "m3"); err != nil {
		t.Fatalf("under5 → pharmacy: %v", err)
	}
}

func TestEnvelopeExpiryAndSweep(t *testing.T) {
	w, f := newCitizenWallet()
	// expires_in_days = -1 → already expired (test clock shortcut via
	// funding with a negative horizon is rejected; use 0 then backdate).
	if _, _, err := w.FundCitizen("NIN-5", "EMERGENCY", 8000_00, "NEMTC-FUND", "b4", 1); err != nil {
		t.Fatal(err)
	}
	acct := "CITIZEN-NIN-5:EMERGENCY"
	rec, _ := w.envReg().get(acct)
	rec.ExpiresAt = rec.ExpiresAt.Add(-48 * 60 * 60 * 1e9 * 24) // backdate 48h
	w.envReg().set(acct, rec)

	if _, _, err := w.CitizenPay("NIN-5", "EMERGENCY", "NEMTC-fleet", 1000_00, "e1"); !errors.Is(err, ErrEnvelopeExpired) {
		t.Fatalf("expired envelope must refuse payment: %v", err)
	}
	if _, err := w.SweepExpired("NIN-5", "EMERGENCY", ""); err == nil {
		t.Fatal("sweep requires an actor")
	}
	if _, err := w.SweepExpired("NIN-5", "EMERGENCY", "treasury-officer"); err != nil {
		t.Fatal(err)
	}
	if f.bal[acct] != 0 || f.bal[prefixGovProg+"NEMTC-FUND"] != 8000_00 {
		t.Fatalf("sweep: env=%d prog=%d", f.bal[acct], f.bal[prefixGovProg+"NEMTC-FUND"])
	}
}

func TestCitizenPayIsExactlyOnce(t *testing.T) {
	w, f := newCitizenWallet()
	w.FundCitizen("NIN-6", "", 5000_00, "BHCPF", "b5", 0)
	w.CitizenPay("NIN-6", "GENERAL", "WALLET-phc-1", 1000_00, "p1")
	_, dup, err := w.CitizenPay("NIN-6", "GENERAL", "WALLET-phc-1", 1000_00, "p1")
	if err != nil || !dup {
		t.Fatalf("retry must dedupe: %v dup=%v", err, dup)
	}
	if f.bal["CITIZEN-NIN-6"] != 4000_00 {
		t.Fatalf("double charge! balance = %d", f.bal["CITIZEN-NIN-6"])
	}
}

func TestCitizenValidation(t *testing.T) {
	w, _ := newCitizenWallet()
	if _, _, err := w.FundCitizen("", "", 100, "P", "r1", 0); !errors.Is(err, ErrBadNIN) {
		t.Fatal("nin required")
	}
	if _, _, err := w.FundCitizen("NIN-7", "HOLIDAY", 100, "P", "r2", 0); !errors.Is(err, ErrUnknownEnvelope) {
		t.Fatal("unknown envelope refused")
	}
	if _, _, err := w.FundCitizen("NIN-7", "", 100, "P", "", 0); !errors.Is(err, ErrRefRequired) {
		t.Fatal("ref required")
	}
	if _, _, err := w.CitizenPay("NIN-7", "", "WALLET-x", -5, "r3"); !errors.Is(err, ErrBadAmount) {
		t.Fatal("bad amount refused")
	}
}
