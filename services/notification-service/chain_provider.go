// Failover SMS provider chain (ADR-0015) — the Notifications "A" row.
//
// One aggregator is a single point of failure sitting on the OTP and
// results-ready paths; Nigerian SMS aggregators degrade often enough
// that "provider credentials" was never the real gap — failover was.
// The chain walks providers in order; each leg is wrapped in a circuit
// breaker so a dead aggregator costs one fast rejection instead of an
// 8-second timeout per message:
//
//	closed   → send through the leg
//	open     → skip instantly (after maxFailures consecutive failures)
//	half-open→ one probe after cooldown; success closes, failure re-opens
//
// If every leg fails, the error names each leg's failure so the drain
// loop's retry and the ops dashboard can see the whole story.
package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"
)

type breaker struct {
	maxFailures int
	cooldown    time.Duration

	mu        sync.Mutex
	failures  int
	openedAt  time.Time
	probeOut  bool // half-open probe in flight
	succeeded int
	attempted int
}

// allow reports whether a call may proceed now.
func (b *breaker) allow(now time.Time) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.failures < b.maxFailures {
		return true // closed
	}
	if now.Sub(b.openedAt) < b.cooldown {
		return false // open
	}
	if b.probeOut {
		return false // half-open, probe already in flight
	}
	b.probeOut = true
	return true
}

func (b *breaker) success() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures = 0
	b.probeOut = false
	b.succeeded++
	b.attempted++
}

func (b *breaker) failure(now time.Time) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures++
	b.attempted++
	b.probeOut = false
	if b.failures >= b.maxFailures {
		b.openedAt = now
	}
}

// stateLocked reports the breaker state; caller must hold b.mu.
func (b *breaker) stateLocked(now time.Time) string {
	switch {
	case b.failures < b.maxFailures:
		return "closed"
	case now.Sub(b.openedAt) >= b.cooldown:
		return "half-open"
	default:
		return "open"
	}
}

type chainLeg struct {
	name string
	p    SMSProvider
	br   *breaker
}

// ChainProvider is an ordered failover chain of SMS providers.
type ChainProvider struct {
	legs []chainLeg
	now  func() time.Time
}

// NewChainProvider wraps each provider in a circuit breaker.
// maxFailures <= 0 defaults to 3; cooldown <= 0 defaults to 60s.
func NewChainProvider(maxFailures int, cooldown time.Duration, providers ...SMSProvider) *ChainProvider {
	if maxFailures <= 0 {
		maxFailures = 3
	}
	if cooldown <= 0 {
		cooldown = 60 * time.Second
	}
	c := &ChainProvider{now: time.Now}
	for _, p := range providers {
		c.legs = append(c.legs, chainLeg{
			name: p.Name(),
			p:    p,
			br:   &breaker{maxFailures: maxFailures, cooldown: cooldown},
		})
	}
	return c
}

func (c *ChainProvider) Name() string {
	names := make([]string, len(c.legs))
	for i, l := range c.legs {
		names[i] = l.name
	}
	return "chain(" + strings.Join(names, ",") + ")"
}

func (c *ChainProvider) Send(phone, text string) error {
	if len(c.legs) == 0 {
		return fmt.Errorf("sms chain: no providers configured")
	}
	now := c.now()
	var errs []string
	for i := range c.legs {
		l := &c.legs[i]
		if !l.br.allow(now) {
			errs = append(errs, l.name+": circuit open")
			continue
		}
		if err := l.p.Send(phone, text); err != nil {
			l.br.failure(now)
			errs = append(errs, l.name+": "+err.Error())
			continue
		}
		l.br.success()
		return nil
	}
	return fmt.Errorf("all sms providers failed [%s]", strings.Join(errs, "; "))
}

// providerFromEnv builds the delivery chain from the environment:
//
//	SMS_PROVIDERS   ordered comma list, e.g. "webhook:https://termii.example/api,webhook:https://at.example/sms,log"
//	                legs: log | webhook:<url>
//	SMS_WEBHOOK_TOKEN    bearer token for the first webhook leg
//	SMS_WEBHOOK_TOKEN_2  ... second webhook leg, etc.
//	SMS_WEBHOOK_URL      legacy single-webhook shorthand (still honored)
//	SMS_BREAKER_FAILURES / SMS_BREAKER_COOLDOWN_SECONDS tune the breakers
//
// Default (nothing set): a single log leg — identical behavior to the
// pre-chain default, just with a breaker and status around it.
func providerFromEnv() SMSProvider {
	maxFailures := 3
	if v := os.Getenv("SMS_BREAKER_FAILURES"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			maxFailures = n
		}
	}
	cooldown := 60 * time.Second
	if v := os.Getenv("SMS_BREAKER_COOLDOWN_SECONDS"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			cooldown = time.Duration(n) * time.Second
		}
	}

	spec := os.Getenv("SMS_PROVIDERS")
	if spec == "" {
		if wh := os.Getenv("SMS_WEBHOOK_URL"); wh != "" {
			spec = "webhook:" + wh
		} else {
			spec = "log"
		}
	}

	var legs []SMSProvider
	webhookN := 0
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		switch {
		case part == "log":
			legs = append(legs, &LogProvider{})
		case strings.HasPrefix(part, "webhook:"):
			webhookN++
			token := os.Getenv(fmt.Sprintf("SMS_WEBHOOK_TOKEN_%d", webhookN))
			if token == "" {
				token = os.Getenv("SMS_WEBHOOK_TOKEN")
			}
			legs = append(legs, NewWebhookProvider(strings.TrimPrefix(part, "webhook:"), token))
		case part == "":
			// tolerate trailing commas
		default:
			// An unknown leg in production config must be loud, not skipped.
			legs = append(legs, &brokenProvider{name: "unknown(" + part + ")"})
		}
	}
	return NewChainProvider(maxFailures, cooldown, legs...)
}

// brokenProvider is a config-error leg: every Send fails, so a typo'd
// SMS_PROVIDERS entry shows up in drain errors and /providers instead of
// silently shrinking the chain.
type brokenProvider struct{ name string }

func (b *brokenProvider) Name() string { return b.name }
func (b *brokenProvider) Send(_, _ string) error {
	return fmt.Errorf("unknown SMS provider spec — fix SMS_PROVIDERS")
}

// LegStatus is the ops view of one chain leg (health endpoint).
type LegStatus struct {
	Name      string `json:"name"`
	State     string `json:"state"` // closed | open | half-open
	Failures  int    `json:"consecutive_failures"`
	Attempted int    `json:"attempted"`
	Succeeded int    `json:"succeeded"`
}

// Status snapshots every leg — the answer to "is the aggregator down?"
// without reading logs.
func (c *ChainProvider) Status() []LegStatus {
	now := c.now()
	out := make([]LegStatus, len(c.legs))
	for i, l := range c.legs {
		l.br.mu.Lock()
		out[i] = LegStatus{
			Name:      l.name,
			State:     l.br.stateLocked(now),
			Failures:  l.br.failures,
			Attempted: l.br.attempted,
			Succeeded: l.br.succeeded,
		}
		l.br.mu.Unlock()
	}
	return out
}
