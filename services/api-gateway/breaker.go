// Per-upstream circuit breakers + request-id (ADR-0015) — the APISIX row.
//
// A reverse proxy without breakers turns one sick upstream into a
// platform-wide stall: every request pays the dial/read timeout before
// its 502, and under load the gateway's own sockets exhaust waiting.
// The fix is the same closed/open/half-open pattern as the SMS chain
// (notification-service/chain_provider.go):
//
//	closed    → proxy through; upstream errors and 5xx count as failures
//	open      → after maxFailures consecutive failures, reject instantly
//	            with 503 + Retry-After (one fast rejection, no upstream wait)
//	half-open → after cooldown, one probe request; success closes the
//	            circuit, failure re-opens it
//
// State is per logical service (route.Service), not per route, so
// /api/claims and /api/preauth share one view of claims-orchestrator
// health. /api/health surfaces every breaker's state for the ops
// dashboard — "why is the wallet slow?" is answered without log diving.
package main

import (
	"crypto/rand"
	"encoding/hex"
	"net/http"
	"sync"
	"time"
)

// upBreaker is one upstream's circuit breaker.
type upBreaker struct {
	maxFailures int
	cooldown    time.Duration

	mu        sync.Mutex
	failures  int
	openedAt  time.Time
	probeOut  bool // half-open probe in flight
	attempted int
	succeeded int
	rejected  int // requests fast-failed while open
}

func newUpBreaker(maxFailures int, cooldown time.Duration) *upBreaker {
	if maxFailures <= 0 {
		maxFailures = 5
	}
	if cooldown <= 0 {
		cooldown = 30 * time.Second
	}
	return &upBreaker{maxFailures: maxFailures, cooldown: cooldown}
}

// allow reports whether a request may be proxied now.
func (b *upBreaker) allow(now time.Time) bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.failures < b.maxFailures {
		return true // closed
	}
	if now.Sub(b.openedAt) < b.cooldown {
		b.rejected++
		return false // open
	}
	if b.probeOut {
		b.rejected++
		return false // half-open, probe already in flight
	}
	b.probeOut = true
	return true
}

func (b *upBreaker) success() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures = 0
	b.probeOut = false
	b.attempted++
	b.succeeded++
}

func (b *upBreaker) failure(now time.Time) {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failures++
	b.attempted++
	b.probeOut = false
	if b.failures >= b.maxFailures {
		b.openedAt = now
	}
}

// state reports closed | open | half-open.
func (b *upBreaker) state(now time.Time) string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.stateLocked(now)
}

func (b *upBreaker) stateLocked(now time.Time) string {
	switch {
	case b.failures < b.maxFailures:
		return "closed"
	case now.Sub(b.openedAt) >= b.cooldown:
		return "half-open"
	default:
		return "open"
	}
}

// BreakerStatus is the ops view of one upstream breaker.
type BreakerStatus struct {
	State     string `json:"state"` // closed | open | half-open
	Failures  int    `json:"consecutive_failures"`
	Attempted int    `json:"attempted"`
	Succeeded int    `json:"succeeded"`
	Rejected  int    `json:"fast_rejected"`
}

func (b *upBreaker) status(now time.Time) BreakerStatus {
	b.mu.Lock()
	defer b.mu.Unlock()
	return BreakerStatus{
		State:     b.stateLocked(now),
		Failures:  b.failures,
		Attempted: b.attempted,
		Succeeded: b.succeeded,
		Rejected:  b.rejected,
	}
}

// ---------- request id ----------

// requestID ensures every request carries an X-Request-ID: the client's
// own if present (propagated so a whole call chain shares one id), else a
// fresh 128-bit random id. The id is set on the inbound request (so the
// proxy Director forwards it upstream) and echoed on the response (so
// clients can quote it in bug reports and ops can grep one id across
// gateway + service logs).
func requestID(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := r.Header.Get("X-Request-ID")
		if id == "" {
			var b [16]byte
			if _, err := rand.Read(b[:]); err == nil {
				id = hex.EncodeToString(b[:])
			}
		}
		if id != "" {
			r.Header.Set("X-Request-ID", id)
			w.Header().Set("X-Request-ID", id)
		}
		next.ServeHTTP(w, r)
	})
}
