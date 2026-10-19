package main

import (
	"errors"
	"strings"
	"testing"
)

type fakeWallet struct {
	bal  map[string]int
	paid []string
	fail error
}

func (f *fakeWallet) Balance(nin string) (int, map[string]int, error) {
	b, ok := f.bal[nin]
	if !ok {
		return 0, nil, errors.New("no wallet")
	}
	return b, map[string]int{"GENERAL": b}, nil
}

func (f *fakeWallet) Pay(nin, envelope, dest string, amount int, ref string) error {
	if f.fail != nil {
		return f.fail
	}
	f.paid = append(f.paid, dest)
	f.bal[nin] -= amount
	return nil
}

func walletGateway() (*Gateway, *fakeWallet) {
	fw := &fakeWallet{bal: map[string]int{"NIN-1": 5000_00}}
	return NewGateway(staticChecker{}).WithWallet(fw, fw), fw
}

func TestWalletMenuHiddenWithoutBinding(t *testing.T) {
	g := NewGateway(staticChecker{})
	resp := g.HandleWithPhone("s1", "+234800", "4")
	if !strings.Contains(resp, "not active") {
		t.Fatalf("unbound wallet must answer not-active, got %q", resp)
	}
}

func TestWalletBalanceFlow(t *testing.T) {
	g, _ := walletGateway()
	g.HandleWithPhone("s2", "+234800", "")
	resp := g.HandleWithPhone("s2", "+234800", "4")
	if !strings.Contains(resp, "NIN") {
		t.Fatalf("expected NIN prompt, got %q", resp)
	}
	resp = g.HandleWithPhone("s2", "+234800", "4*NIN-1")
	if !strings.Contains(resp, "₦5000.00") {
		t.Fatalf("balance missing: %q", resp)
	}
	if !strings.Contains(resp, "facility code") {
		t.Fatalf("pay prompt missing: %q", resp)
	}
	resp = g.HandleWithPhone("s2", "+234800", "4*NIN-1*0")
	if resp != "END Thank you." {
		t.Fatalf("exit: %q", resp)
	}
}

func TestWalletPayFlow(t *testing.T) {
	g, fw := walletGateway()
	g.HandleWithPhone("s3", "+234800", "4")
	g.HandleWithPhone("s3", "+234800", "4*NIN-1")
	resp := g.HandleWithPhone("s3", "+234800", "4*NIN-1*phc-karu")
	if !strings.Contains(resp, "amount") {
		t.Fatalf("amount prompt: %q", resp)
	}
	resp = g.HandleWithPhone("s3", "+234800", "4*NIN-1*phc-karu*1500")
	if !strings.Contains(resp, "Paid ₦1500 to phc-karu") {
		t.Fatalf("receipt: %q", resp)
	}
	if fw.paid[0] != "WALLET-phc-karu" || fw.bal["NIN-1"] != 3500_00 {
		t.Fatalf("paid=%v bal=%d", fw.paid, fw.bal["NIN-1"])
	}
}

func TestWalletPayRefusalReadsSafe(t *testing.T) {
	g, fw := walletGateway()
	fw.fail = errors.New("wallet refused (403): not on allow-list")
	g.HandleWithPhone("s4", "+234800", "4")
	g.HandleWithPhone("s4", "+234800", "4*NIN-1")
	g.HandleWithPhone("s4", "+234800", "4*NIN-1*phc-karu")
	resp := g.HandleWithPhone("s4", "+234800", "4*NIN-1*phc-karu*500")
	if !strings.Contains(resp, "Your money is safe") {
		t.Fatalf("refusal must reassure, got %q", resp)
	}
}

func TestUnknownNIN(t *testing.T) {
	g, _ := walletGateway()
	g.HandleWithPhone("s5", "+234800", "4")
	resp := g.HandleWithPhone("s5", "+234800", "4*NIN-UNKNOWN")
	if !strings.Contains(resp, "No health wallet found") {
		t.Fatalf("unknown NIN: %q", resp)
	}
}
