package main

import (
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
)

func econHandler(t *testing.T) economicsHandler {
	t.Helper()
	return economicsHandler{R: SubprocessEconomics{
		Python: "python3",
		Script: resolveToolPath("../../packages/economics/economics.py"),
	}}
}

func TestEconomicsMarginsSeam(t *testing.T) {
	h := econHandler(t)
	body := `{"encounters":[{"code":"DEL-CS","staff":[{"minutes":180,"kobo_per_minute":10000}],"consumables":[{"kobo":2000000}],"los_days":2}],"overhead_kobo_per_day":500000}`
	req := httptest.NewRequest("POST", "/economics/margins", strings.NewReader(body))
	req.SetPathValue("op", "margins")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("status %d: %s", rec.Code, rec.Body)
	}
	var out map[string]any
	json.NewDecoder(rec.Body).Decode(&out)
	if out["tariff_year"].(float64) != 2025 {
		t.Fatalf("vintage missing: %v", out)
	}
	lines := out["lines"].([]any)
	line := lines[0].(map[string]any)
	if line["code"] != "DEL-CS" || line["margin_kobo"].(float64) == 0 {
		t.Fatalf("bad margin line: %v", line)
	}
}

func TestEconomicsTariffSimSeam(t *testing.T) {
	h := econHandler(t)
	body := `{"utilization":[{"code":"DEL-CS","volume":10}],"cpi_path":{"2026":0.15,"2027":0.15},"target_year":2027}`
	req := httptest.NewRequest("POST", "/economics/tariff-sim", strings.NewReader(body))
	req.SetPathValue("op", "tariff-sim")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var out map[string]any
	json.NewDecoder(rec.Body).Decode(&out)
	if rec.Code != 200 || out["pool_outflow_delta_kobo"].(float64) <= 0 {
		t.Fatalf("tariff-sim: %d %v", rec.Code, out)
	}
}

func TestEconomicsDenialsAndPanelSeam(t *testing.T) {
	h := econHandler(t)
	body := `{"claims":[{"hmo":"HMO-A","status":"denied","denial_reason":"missing-referral","amount_kobo":400000}]}`
	req := httptest.NewRequest("POST", "/economics/denials", strings.NewReader(body))
	req.SetPathValue("op", "denials")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	var out map[string]any
	json.NewDecoder(rec.Body).Decode(&out)
	if rec.Code != 200 || out["preventable_share_pct"].(float64) != 100 {
		t.Fatalf("denials: %d %v", rec.Code, out)
	}

	pbody := `{"enrollees":[{"id":"e1","age":30}],"encounters":[{"code":"CONS-GEN","enrollee_id":"e1","staff":[{"minutes":20,"kobo_per_minute":5000}],"consumables":[]}],"capitation_kobo":145000}`
	req = httptest.NewRequest("POST", "/economics/panel", strings.NewReader(pbody))
	req.SetPathValue("op", "panel")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	json.NewDecoder(rec.Body).Decode(&out)
	if rec.Code != 200 || out["panel_size"].(float64) != 1 {
		t.Fatalf("panel: %d %v", rec.Code, out)
	}
}

func TestEconomicsUnknownOpRejected(t *testing.T) {
	h := econHandler(t)
	req := httptest.NewRequest("POST", "/economics/hack", strings.NewReader(`{}`))
	req.SetPathValue("op", "hack")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 404 {
		t.Fatalf("unknown op: %d", rec.Code)
	}
	req = httptest.NewRequest("GET", "/economics/margins", nil)
	req.SetPathValue("op", "margins")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != 405 {
		t.Fatalf("GET must be rejected: %d", rec.Code)
	}
}
