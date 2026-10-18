package main

import (
	"errors"
	"strings"
	"testing"
	"time"
)

type fakeProvider struct {
	name string
	err  error
	sent int
}

func (f *fakeProvider) Name() string { return f.name }
func (f *fakeProvider) Send(_, _ string) error {
	f.sent++
	return f.err
}

func TestChainFailsOverToSecondLeg(t *testing.T) {
	dead := &fakeProvider{name: "primary", err: errors.New("aggregator 502")}
	live := &fakeProvider{name: "backup"}
	c := NewChainProvider(3, time.Minute, dead, live)
	if err := c.Send("+234801", "hello"); err != nil {
		t.Fatalf("chain should deliver via backup, got %v", err)
	}
	if dead.sent != 1 || live.sent != 1 {
		t.Fatalf("want primary tried once then backup once, got %d/%d", dead.sent, live.sent)
	}
}

func TestChainErrorNamesEveryLeg(t *testing.T) {
	a := &fakeProvider{name: "p1", err: errors.New("boom1")}
	b := &fakeProvider{name: "p2", err: errors.New("boom2")}
	c := NewChainProvider(5, time.Minute, a, b)
	err := c.Send("+234801", "x")
	if err == nil || !strings.Contains(err.Error(), "boom1") || !strings.Contains(err.Error(), "boom2") {
		t.Fatalf("error must name every leg's failure: %v", err)
	}
}

func TestBreakerOpensAndSkips(t *testing.T) {
	dead := &fakeProvider{name: "dead", err: errors.New("down")}
	live := &fakeProvider{name: "live"}
	now := time.Now()
	c := NewChainProvider(2, time.Minute, dead, live)
	c.now = func() time.Time { return now }
	for i := 0; i < 2; i++ {
		if err := c.Send("+234801", "x"); err != nil {
			t.Fatal(err)
		}
	}
	if dead.sent != 2 {
		t.Fatalf("dead leg should have been tried twice, got %d", dead.sent)
	}
	// Third send: breaker open → dead leg skipped instantly, no 8s timeout.
	if err := c.Send("+234801", "x"); err != nil {
		t.Fatal(err)
	}
	if dead.sent != 2 {
		t.Fatalf("open breaker must skip the dead leg, got %d attempts", dead.sent)
	}
	st := c.Status()
	if st[0].State != "open" || st[1].State != "closed" {
		t.Fatalf("status: %+v", st)
	}
}

func TestBreakerHalfOpenProbeRecovers(t *testing.T) {
	flaky := &fakeProvider{name: "flaky", err: errors.New("down")}
	now := time.Now()
	c := NewChainProvider(1, time.Minute, flaky)
	c.now = func() time.Time { return now }
	if err := c.Send("+234801", "x"); err == nil {
		t.Fatal("first send should fail")
	}
	// Advance past cooldown: half-open probe is allowed through.
	now = now.Add(2 * time.Minute)
	flaky.err = nil // aggregator recovered
	if err := c.Send("+234801", "x"); err != nil {
		t.Fatalf("probe should succeed after recovery, got %v", err)
	}
	if st := c.Status()[0]; st.State != "closed" || st.Failures != 0 {
		t.Fatalf("breaker should be closed after probe success: %+v", st)
	}
}

func TestHalfOpenProbeFailureReopens(t *testing.T) {
	dead := &fakeProvider{name: "dead", err: errors.New("still down")}
	now := time.Now()
	c := NewChainProvider(1, time.Minute, dead)
	c.now = func() time.Time { return now }
	_ = c.Send("+234801", "x")     // opens breaker
	now = now.Add(2 * time.Minute) // cooldown elapses
	if err := c.Send("+234801", "x"); err == nil {
		t.Fatal("probe should fail")
	}
	attempts := dead.sent
	if err := c.Send("+234801", "x"); err == nil {
		t.Fatal("re-opened breaker must reject immediately")
	}
	if dead.sent != attempts {
		t.Fatal("re-opened breaker must not call the provider again")
	}
}

func TestProviderFromEnvDefaultIsLogChain(t *testing.T) {
	t.Setenv("SMS_PROVIDERS", "")
	t.Setenv("SMS_WEBHOOK_URL", "")
	p := providerFromEnv()
	chain, ok := p.(*ChainProvider)
	if !ok {
		t.Fatalf("default should be a chain, got %T", p)
	}
	if len(chain.legs) != 1 || chain.legs[0].name != "log" {
		t.Fatalf("default chain should be one log leg: %+v", chain.legs)
	}
	if err := p.Send("+234801", "x"); err != nil {
		t.Fatal(err)
	}
}

func TestProviderFromEnvBuildsOrderedChain(t *testing.T) {
	t.Setenv("SMS_PROVIDERS", "webhook:https://a.example/sms, webhook:https://b.example/sms ,log")
	t.Setenv("SMS_WEBHOOK_TOKEN", "tok1")
	t.Setenv("SMS_WEBHOOK_TOKEN_2", "tok2")
	p := providerFromEnv()
	chain := p.(*ChainProvider)
	if len(chain.legs) != 3 {
		t.Fatalf("want 3 legs, got %d", len(chain.legs))
	}
	w1 := chain.legs[0].p.(*WebhookProvider)
	w2 := chain.legs[1].p.(*WebhookProvider)
	if w1.url != "https://a.example/sms" || w1.token != "tok1" {
		t.Fatalf("leg1: %+v", w1)
	}
	if w2.url != "https://b.example/sms" || w2.token != "tok2" {
		t.Fatalf("leg2: %+v", w2)
	}
	if chain.legs[2].name != "log" {
		t.Fatalf("leg3 should be log, got %s", chain.legs[2].name)
	}
}

func TestProviderFromEnvUnknownSpecIsLoudNotSilent(t *testing.T) {
	t.Setenv("SMS_PROVIDERS", "pigeon:coop")
	p := providerFromEnv()
	if err := p.Send("+234801", "x"); err == nil || !strings.Contains(err.Error(), "unknown SMS provider") {
		t.Fatalf("unknown spec must fail loudly: %v", err)
	}
}

func TestProviderFromEnvLegacyWebhookURL(t *testing.T) {
	t.Setenv("SMS_PROVIDERS", "")
	t.Setenv("SMS_WEBHOOK_URL", "https://legacy.example/sms")
	t.Setenv("SMS_WEBHOOK_TOKEN", "legtok")
	p := providerFromEnv()
	chain := p.(*ChainProvider)
	if len(chain.legs) != 1 {
		t.Fatalf("legacy config should build one webhook leg, got %d", len(chain.legs))
	}
	w := chain.legs[0].p.(*WebhookProvider)
	if w.url != "https://legacy.example/sms" || w.token != "legtok" {
		t.Fatalf("legacy leg: %+v", w)
	}
}
