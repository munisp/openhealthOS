package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func testGateway(t *testing.T, upstreamHandler http.Handler) (*gateway, *httptest.Server) {
	t.Helper()
	up := httptest.NewServer(upstreamHandler)
	t.Cleanup(up.Close)
	routes := []Route{{Prefix: "/api/claims", Service: "claims", Upstream: up.URL, Strip: true}}
	return newGateway(routes, newRateLimiter(10, 20)), up
}

func TestRoutesAndStripsPrefix(t *testing.T) {
	var gotPath string
	g, _ := testGateway(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.Write([]byte(`{"ok":true}`))
	}))
	srv := httptest.NewServer(g)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/claims/eligibility?member=NG-0001")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("proxy: %s", resp.Status)
	}
	if gotPath != "/eligibility" {
		t.Fatalf("prefix not stripped: %q", gotPath)
	}
}

func TestUnknownRoute404(t *testing.T) {
	g, _ := testGateway(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
	srv := httptest.NewServer(g)
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/api/nope")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("want 404, got %s", resp.Status)
	}
}

func TestRateLimit429(t *testing.T) {
	g, _ := testGateway(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
	g.limiter = newRateLimiter(0.0001, 3) // 3-request burst, ~no refill
	srv := httptest.NewServer(g)
	defer srv.Close()
	codes := map[int]int{}
	for i := 0; i < 10; i++ {
		resp, err := http.Get(srv.URL + "/api/claims/x")
		if err != nil {
			t.Fatal(err)
		}
		codes[resp.StatusCode]++
		resp.Body.Close()
	}
	if codes[200] > 3 || codes[429] == 0 {
		t.Fatalf("rate limit not enforced: %v", codes)
	}
}

func TestHealthMatrix(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" {
			w.Write([]byte("ok"))
			return
		}
	}))
	defer up.Close()
	g := newGateway([]Route{{Prefix: "/api/x", Service: "svc-x", Upstream: up.URL, Strip: true}}, newRateLimiter(10, 20))
	srv := httptest.NewServer(g)
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var matrix map[string]struct {
		Up bool `json:"up"`
	}
	json.NewDecoder(resp.Body).Decode(&matrix)
	if !matrix["svc-x"].Up {
		t.Fatalf("health matrix wrong: %v", matrix)
	}
}

func TestUpstreamFailure502(t *testing.T) {
	g := newGateway([]Route{{Prefix: "/api/dead", Service: "dead", Upstream: "http://127.0.0.1:1", Strip: true}},
		newRateLimiter(10, 20))
	srv := httptest.NewServer(g)
	defer srv.Close()
	resp, err := http.Get(srv.URL + "/api/dead/x")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadGateway {
		t.Fatalf("want 502, got %s", resp.Status)
	}
}

// TestPrefixRewrite pins the /api/fhir → /fhir rewrite (and the
// /api/fhir/inbox intake): the upstream FHIR services serve /fhir/...,
// so the gateway must rewrite, not merely strip or pass through.
func TestPrefixRewrite(t *testing.T) {
	var gotPath string
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.Write([]byte("{}"))
	}))
	defer up.Close()
	g := newGateway([]Route{
		{Prefix: "/api/fhir/inbox", Service: "notification-service", Upstream: up.URL, ToPrefix: "/fhir/inbox"},
		{Prefix: "/api/fhir", Service: "shr-service", Upstream: up.URL, ToPrefix: "/fhir"},
	}, newRateLimiter(100, 200))
	srv := httptest.NewServer(g)
	defer srv.Close()

	resp, err := http.Post(srv.URL+"/api/fhir/inbox", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if gotPath != "/fhir/inbox" {
		t.Fatalf("inbox rewrite wrong: %q", gotPath)
	}

	resp2, err := http.Get(srv.URL + "/api/fhir/Encounter/e-1")
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if gotPath != "/fhir/Encounter/e-1" {
		t.Fatalf("fhir rewrite wrong: %q", gotPath)
	}
}
