package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func testServer() *Server {
	s := &Server{timeout: 2 * time.Second, now: time.Now, providers: map[string]Provider{}}
	s.providers["cpoe-order-screen"] = s.cpoeProvider
	s.providers["immig-defaulter-view"] = s.immigProvider
	return s
}

func do(s *Server, method, path string, body any) *httptest.ResponseRecorder {
	b, _ := json.Marshal(body)
	r := httptest.NewRequest(method, path, bytes.NewReader(b))
	w := httptest.NewRecorder()
	mux := http.NewServeMux()
	s.routes(mux)
	mux.ServeHTTP(w, r)
	return w
}

func TestDiscovery(t *testing.T) {
	w := do(testServer(), "GET", "/cds-services", nil)
	if w.Code != 200 {
		t.Fatalf("code %d", w.Code)
	}
	var out struct{ Services []HookService }
	json.Unmarshal(w.Body.Bytes(), &out)
	if len(out.Services) != 2 {
		t.Fatalf("want 2 services, got %d", len(out.Services))
	}
}

func TestOrderScreenAllergyBlocks(t *testing.T) {
	w := do(testServer(), "POST", "/cds-services/cpoe-order-screen", map[string]any{
		"context": map[string]any{"drugCode": "NG-AMOX500", "allergies": []string{"penicillin"},
			"activeMedications": []string{}},
	})
	var resp HookResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	if len(resp.Cards) == 0 || resp.Cards[0].Indicator != "critical" {
		t.Fatalf("expected blocking allergy card, got %+v", resp)
	}
}

func TestOrderScreenInteractionWarns(t *testing.T) {
	w := do(testServer(), "POST", "/cds-services/cpoe-order-screen", map[string]any{
		"context": map[string]any{"drugCode": "NG-IBU400", "allergies": []string{},
			"activeMedications": []string{"NG-LISIN10"}},
	})
	var resp HookResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	found := false
	for _, c := range resp.Cards {
		if c.Indicator == "warning" {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected interaction warning, got %+v", resp)
	}
}

func TestImmigDefaulterCards(t *testing.T) {
	w := do(testServer(), "POST", "/cds-services/immig-defaulter-view", map[string]any{
		"context": map[string]any{"birthDate": "2026-01-01"},
	})
	var resp HookResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	if len(resp.Cards) == 0 {
		t.Fatalf("expected due/overdue cards for 9-month-old with no history")
	}
}

func TestFailSoftOnMissingBirthdate(t *testing.T) {
	w := do(testServer(), "POST", "/cds-services/immig-defaulter-view", map[string]any{
		"context": map[string]any{},
	})
	var resp HookResponse
	json.Unmarshal(w.Body.Bytes(), &resp)
	if len(resp.Warnings) == 0 || len(resp.Cards) != 0 {
		t.Fatalf("expected fail-soft warning with empty cards, got %+v", resp)
	}
}

func TestUnknownService404(t *testing.T) {
	w := do(testServer(), "POST", "/cds-services/nope", map[string]any{"context": map[string]any{}})
	if w.Code != http.StatusNotFound {
		t.Fatalf("want 404, got %d", w.Code)
	}
}
