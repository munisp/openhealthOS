package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestKPIPlaneServedWhenBuilt(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "kpi_plane.json")
	if err := os.WriteFile(p, []byte(`{"sections":{"panel_risk":{"status":"ok"}}}`), 0o644); err != nil {
		t.Fatal(err)
	}
	g := newGateway(nil, newRateLimiter(100, 100))
	g.kpiPath = p

	rec := httptest.NewRecorder()
	g.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/kpi/plane", nil))
	if rec.Code != 200 {
		t.Fatalf("want 200, got %d", rec.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("not json: %v", err)
	}
	if _, ok := body["sections"]; !ok {
		t.Fatal("expected sections in kpi plane body")
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Fatalf("content-type %q", ct)
	}
}

func TestKPIPlane404BeforeNightlyBuild(t *testing.T) {
	g := newGateway(nil, newRateLimiter(100, 100))
	g.kpiPath = filepath.Join(t.TempDir(), "missing.json")
	rec := httptest.NewRecorder()
	g.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/kpi/plane", nil))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("want 404, got %d", rec.Code)
	}
}

func TestKPIPlaneNotConfigured(t *testing.T) {
	g := newGateway(nil, newRateLimiter(100, 100))
	rec := httptest.NewRecorder()
	g.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/api/kpi/plane", nil))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("want 404, got %d", rec.Code)
	}
}

func TestKPIPlaneGetOnly(t *testing.T) {
	g := newGateway(nil, newRateLimiter(100, 100))
	g.kpiPath = "x.json"
	rec := httptest.NewRecorder()
	g.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/api/kpi/plane", nil))
	if rec.Code != http.StatusMethodNotAllowed {
		t.Fatalf("want 405, got %d", rec.Code)
	}
}
