package main

import (
	"errors"
	"testing"
)

func TestGiftFundingAndRestriction(t *testing.T) {
	w, f := newCitizenWallet()
	// Diaspora gift: giver in Houston pays by card; PSP settles naira.
	tx, dup, err := w.GiftCitizen("NIN-9", "", 20000_00, "giver-houston-1", "psp-rcpt-77", 0)
	if err != nil || dup || tx == "" {
		t.Fatalf("gift: %v dup=%v", err, dup)
	}
	if f.bal["CITIZEN-NIN-9"] != 20000_00 {
		t.Fatalf("gifted balance = %d", f.bal["CITIZEN-NIN-9"])
	}
	// Attribution lives in the tx id.
	found := false
	for _, p := range f.posts {
		if p == "GIFT-giver-houston-1-psp-rcpt-77" {
			found = true
		}
	}
	if !found {
		t.Fatalf("gift tx not attributed: %v", f.posts)
	}

	// The gift inherits the allow-list: clinic — yes; airtime — no.
	if _, _, err := w.CitizenPay("NIN-9", "GENERAL", "WALLET-phc-karu", 5000_00, "g1"); err != nil {
		t.Fatalf("gift → facility: %v", err)
	}
	if _, _, err := w.CitizenPay("NIN-9", "GENERAL", "AIRTIME-MTN", 1000_00, "g2"); !errors.Is(err, ErrRestrictedDestination) {
		t.Fatalf("gift → airtime must be refused: %v", err)
	}

	// A gift can target an envelope (giver earmarks it maternal).
	if _, _, err := w.GiftCitizen("NIN-9", "MATERNAL", 5000_00, "aunt-london", "psp-rcpt-78", 180); err != nil {
		t.Fatal(err)
	}
	if _, _, err := w.CitizenPay("NIN-9", "MATERNAL", "PHARM-x", 100_00, "g3"); !errors.Is(err, ErrRestrictedDestination) {
		t.Fatal("maternal gift must keep the maternal policy")
	}
}

func TestGiftIsExactlyOnceAndNeedsGiver(t *testing.T) {
	w, f := newCitizenWallet()
	w.GiftCitizen("NIN-10", "", 3000_00, "uncle-abuja", "r-1", 0)
	_, dup, err := w.GiftCitizen("NIN-10", "", 3000_00, "uncle-abuja", "r-1", 0)
	if err != nil || !dup {
		t.Fatalf("gift retry must dedupe: %v dup=%v", err, dup)
	}
	if f.bal["CITIZEN-NIN-10"] != 3000_00 {
		t.Fatalf("double gift! balance = %d", f.bal["CITIZEN-NIN-10"])
	}
	if _, _, err := w.GiftCitizen("NIN-10", "", 100, "", "r-2", 0); err == nil {
		t.Fatal("giver attribution required")
	}
}
