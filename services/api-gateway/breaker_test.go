package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// An upstream that always dies at the TCP level trips the breaker after
// maxFailures; further requests are rejected with 503 without touching
// the upstream.
func TestBreakerOpensAndFastFails(t *testing.T) {
	var hits int64
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		atomic.AddInt64(&hits, 1)
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer up.Close()

	g := newGateway([]Route{{Prefix: "/api/x", Service: "svc", Upstream: up.URL, Strip: true}},
		newRateLimiter(1000, 1000))
	now := time.Now()
	g.now = func() time.Time { return now }
	srv := httptest.NewServer(g)
	defer srv.Close()

	for i := 0; i < 5; i++ {
		resp, err := http.Get(srv.URL + "/api/x/thing")
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 500 {
			t.Fatalf("call %d: want proxied 500, got %s", i, resp.Status)
		}
	}
	if got := atomic.LoadInt64(&hits); got != 5 {
		t.Fatalf("upstream hits = %d, want 5", got)
	}
	// Sixth call: circuit open → fast 503, upstream untouched.
	resp, err := http.Get(srv.URL + "/api/x/thing")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("open circuit: want 503, got %s", resp.Status)
	}
	if resp.Header.Get("Retry-After") == "" {
		t.Fatal("open circuit must set Retry-After")
	}
	if got := atomic.LoadInt64(&hits); got != 5 {
		t.Fatalf("upstream hit while open: hits = %d", got)
	}
	if st := g.breakerFor("svc").state(now); st != "open" {
		t.Fatalf("state = %q, want open", st)
	}
}

// After cooldown the breaker admits one probe; a healthy upstream closes
// the circuit and traffic flows again.
func TestBreakerHalfOpenRecovery(t *testing.T) {
	var fail int64
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		if atomic.LoadInt64(&fail) == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.Write([]byte(`{"ok":true}`))
	}))
	defer up.Close()

	g := newGateway([]Route{{Prefix: "/api/x", Service: "svc", Upstream: up.URL, Strip: true}},
		newRateLimiter(1000, 1000))
	now := time.Now()
	g.now = func() time.Time { return now }
	srv := httptest.NewServer(g)
	defer srv.Close()

	atomic.StoreInt64(&fail, 1)
	for i := 0; i < 5; i++ {
		resp, _ := http.Get(srv.URL + "/api/x")
		resp.Body.Close()
	}
	// Open. Heal the upstream and advance past cooldown.
	atomic.StoreInt64(&fail, 0)
	now = now.Add(31 * time.Second)

	resp, err := http.Get(srv.URL + "/api/x")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("probe: want 200, got %s", resp.Status)
	}
	if st := g.breakerFor("svc").state(now); st != "closed" {
		t.Fatalf("after probe: state = %q, want closed", st)
	}
}

// 4xx responses are client faults and must never trip the breaker.
func TestBreakerIgnores4xx(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	defer up.Close()
	g := newGateway([]Route{{Prefix: "/api/x", Service: "svc", Upstream: up.URL, Strip: true}},
		newRateLimiter(1000, 1000))
	srv := httptest.NewServer(g)
	defer srv.Close()
	for i := 0; i < 8; i++ {
		resp, err := http.Get(srv.URL + "/api/x/missing")
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 404 {
			t.Fatalf("want proxied 404, got %s", resp.Status)
		}
	}
	if st := g.breakerFor("svc").state(time.Now()); st != "closed" {
		t.Fatalf("4xx tripped breaker: state = %q", st)
	}
}

// Request IDs: a client-supplied id is echoed and forwarded; without one
// the gateway mints one and both sides see it.
func TestRequestIDPropagation(t *testing.T) {
	var gotUpstream string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotUpstream = r.Header.Get("X-Request-ID")
		w.Write([]byte(`{}`))
	}))
	defer up.Close()
	g := newGateway([]Route{{Prefix: "/api/x", Service: "svc", Upstream: up.URL, Strip: true}},
		newRateLimiter(1000, 1000))
	srv := httptest.NewServer(requestID(g))
	defer srv.Close()

	req, _ := http.NewRequest("GET", srv.URL+"/api/x/a", nil)
	req.Header.Set("X-Request-ID", "trace-me-123")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.Header.Get("X-Request-ID") != "trace-me-123" {
		t.Fatalf("echo = %q", resp.Header.Get("X-Request-ID"))
	}
	if gotUpstream != "trace-me-123" {
		t.Fatalf("upstream saw %q", gotUpstream)
	}

	resp2, err := http.Get(srv.URL + "/api/x/b")
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	minted := resp2.Header.Get("X-Request-ID")
	if minted == "" || minted != gotUpstream {
		t.Fatalf("minted id %q, upstream saw %q", minted, gotUpstream)
	}
}

// /api/health surfaces breaker state per service.
func TestHealthIncludesBreakerState(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer up.Close()
	g := newGateway([]Route{{Prefix: "/api/x", Service: "svc", Upstream: up.URL, Strip: true}},
		newRateLimiter(1000, 1000))
	srv := httptest.NewServer(g)
	defer srv.Close()

	for i := 0; i < 5; i++ {
		resp, _ := http.Get(srv.URL + "/api/x")
		resp.Body.Close()
	}
	resp, err := http.Get(srv.URL + "/api/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]struct {
		Breaker BreakerStatus `json:"breaker"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if out["svc"].Breaker.State != "open" {
		t.Fatalf("health breaker state = %q, want open", out["svc"].Breaker.State)
	}
	if out["svc"].Breaker.Rejected == 0 && out["svc"].Breaker.Attempted != 5 {
		t.Fatalf("health breaker counters wrong: %+v", out["svc"].Breaker)
	}
}
