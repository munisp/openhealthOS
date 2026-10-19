package main

import (
	"errors"
	"strings"
	"testing"
)

type fakeBenefits struct {
	headline string
	err      error
	scheme   string // recorded
}

func (f *fakeBenefits) Explain(scheme string) (string, string, error) {
	f.scheme = scheme
	if f.err != nil {
		return "", "", f.err
	}
	return f.headline, "Specialist consults need a referral.", nil
}

func TestBenefitsMenuFlow(t *testing.T) {
	fb := &fakeBenefits{headline: "14 covered services; co-pay 10% at point of care"}
	g := NewGateway(staticChecker{}).WithBenefits(fb)
	resp := g.HandleWithPhone("b1", "+234800", "")
	if !strings.Contains(resp, "cover include") {
		t.Fatalf("menu missing benefits option: %q", resp)
	}
	resp = g.HandleWithPhone("b1", "+234800", "5")
	if !strings.Contains(resp, "member ID") {
		t.Fatalf("expected member prompt: %q", resp)
	}
	resp = g.HandleWithPhone("b1", "+234800", "5*NG-0001")
	if fb.scheme != "STATE-BASIC" {
		t.Fatalf("scheme resolved = %q", fb.scheme)
	}
	if !strings.Contains(resp, "14 covered services") || !strings.Contains(resp, "referral") {
		t.Fatalf("summary: %q", resp)
	}
}

func TestBenefitsHiddenWithoutBinding(t *testing.T) {
	g := NewGateway(staticChecker{})
	resp := g.HandleWithPhone("b2", "+234800", "5")
	if !strings.Contains(resp, "not active") {
		t.Fatalf("unbound benefits: %q", resp)
	}
}

func TestBenefitsUnknownMember(t *testing.T) {
	fb := &fakeBenefits{headline: "x"}
	g := NewGateway(staticChecker{}).WithBenefits(fb)
	g.HandleWithPhone("b3", "+234800", "5")
	resp := g.HandleWithPhone("b3", "+234800", "5*")
	if !strings.Contains(resp, "could not find") {
		t.Fatalf("unknown member: %q", resp)
	}
}

func TestBenefitsServiceDown(t *testing.T) {
	fb := &fakeBenefits{err: errors.New("connection refused")}
	g := NewGateway(staticChecker{}).WithBenefits(fb)
	g.HandleWithPhone("b4", "+234800", "5")
	resp := g.HandleWithPhone("b4", "+234800", "5*NG-0001")
	if !strings.Contains(resp, "unavailable") {
		t.Fatalf("degraded read: %q", resp)
	}
}
