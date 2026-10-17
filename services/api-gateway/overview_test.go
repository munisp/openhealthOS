package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestOverviewAggregatesUpAndDown(t *testing.T) {
	up := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/metrics" {
			w.Write([]byte(`{"requests_total": 42, "errors_4xx": 1, "errors_5xx": 0, "latency_ms_mean": 3.5, "uptime_seconds": 60}`))
			return
		}
		w.Write([]byte("ok"))
	}))
	defer up.Close()

	g := newGateway(nil, newRateLimiter(10, 20))
	g.metrics = map[string]string{
		"claims-orchestrator": up.URL,
		"dead-service":        "http://127.0.0.1:1", // nothing listens
	}
	srv := httptest.NewServer(g)
	defer srv.Close()

	resp, err := http.Get(srv.URL + "/api/platform/overview")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]struct {
		Up            bool  `json:"up"`
		RequestsTotal int64 `json:"requests_total"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if !out["claims-orchestrator"].Up || out["claims-orchestrator"].RequestsTotal != 42 {
		t.Fatalf("live service not aggregated: %+v", out["claims-orchestrator"])
	}
	if out["dead-service"].Up {
		t.Fatal("dead service must be reported down, not omitted or fatal")
	}
}

func TestServeAppMapsPwaAndDash(t *testing.T) {
	root := t.TempDir()
	for _, sub := range []string{"pwa", "dashboards"} {
		os.MkdirAll(filepath.Join(root, sub, "js"), 0o755)
		os.WriteFile(filepath.Join(root, sub, "index.html"), []byte(sub+"-shell"), 0o644)
		os.WriteFile(filepath.Join(root, sub, "js", "app.js"), []byte(sub+"-js"), 0o644)
	}
	g := newGateway(nil, newRateLimiter(10, 20))
	g.appsRoot = root
	srv := httptest.NewServer(g)
	defer srv.Close()

	cases := map[string]string{
		"/":                 "pwa-shell",
		"/app/":             "pwa-shell",
		"/app/js/app.js":    "pwa-js",
		"/dash":             "dashboards-shell",
		"/dash/js/app.js":   "dashboards-js",
		"/dash/../../etc/x": "", // traversal must not escape the root
	}
	for path, want := range cases {
		resp, err := http.Get(srv.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		body := make([]byte, 64)
		n, _ := resp.Body.Read(body)
		resp.Body.Close()
		if want == "" {
			if resp.StatusCode == 200 && n > 0 {
				t.Fatalf("traversal %q served content", path)
			}
			continue
		}
		if string(body[:n]) != want {
			t.Fatalf("%s: got %q want %q", path, body[:n], want)
		}
	}
}

func TestSyncRouteInDefaults(t *testing.T) {
	routes := DefaultRoutes(func(string) string { return "" })
	for _, r := range routes {
		if r.Prefix == "/api/sync" && r.Service == "sync-ingest" {
			return
		}
	}
	t.Fatal("DefaultRoutes must expose sync-ingest at /api/sync for the PWA")
}
