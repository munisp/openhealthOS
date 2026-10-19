package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func testBenefitsServer(t *testing.T) *httptest.Server {
	t.Helper()
	q := SubprocessBenefits{
		Python: "python3",
		Script: resolveToolPath("../../packages/benefits/benefits.py"),
	}
	mux := http.NewServeMux()
	mux.Handle("/benefits/{scheme}", benefitsHandler{Q: q})
	ts := httptest.NewServer(mux)
	t.Cleanup(ts.Close)
	return ts
}

func TestBenefitsExplainOverHTTP(t *testing.T) {
	ts := testBenefitsServer(t)

	resp, err := http.Get(ts.URL + "/benefits/STATE-BASIC")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	if out["scheme"] != "STATE-BASIC" {
		t.Fatalf("explain: %v", out)
	}
	if _, ok := out["categories"]; !ok {
		t.Fatalf("no categories: %v", out)
	}
}

func TestBenefitsCheckOverHTTP(t *testing.T) {
	ts := testBenefitsServer(t)

	resp, _ := http.Get(ts.URL + "/benefits/STATE-BASIC?code=DEL-CS")
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	resp.Body.Close()
	if out["covered"] != true || out["patient_pays_kobo"].(float64) != 750000 {
		t.Fatalf("check: %v", out)
	}
}

func TestBenefitsStalenessOverHTTP(t *testing.T) {
	ts := testBenefitsServer(t)

	resp, _ := http.Get(ts.URL + `/benefits/NG?staleness_year=2027&cpi_path={"2026":0.15,"2027":0.15}`)
	var out map[string]any
	json.NewDecoder(resp.Body).Decode(&out)
	resp.Body.Close()
	if out["cpi_factor"].(float64) != 1.3225 {
		t.Fatalf("staleness: %v", out)
	}
}

func TestBenefitsUnknownScheme(t *testing.T) {
	ts := testBenefitsServer(t)

	resp, err := http.Get(ts.URL + "/benefits/BOGUS")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 422 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}
