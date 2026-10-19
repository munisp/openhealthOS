package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func newTestServer() server {
	ix := Integrations{
		protocol:    defaultTriageProtocol,
		now:         time.Now,
		registryURL: "http://127.0.0.1:1", // unreachable on purpose — assignment is best-effort
		client:      &http.Client{Timeout: 200 * time.Millisecond},
	}
	return server{store: NewMemoryCaseStore(), ix: ix, fleet: NewFleetStore(), mci: NewMCIStore(), voice: newVoiceDialogues()}
}

func post(t *testing.T, s server, path, body string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec, out
}

func TestTriageRedFlagsBeatGeneric(t *testing.T) {
	cat, reason := triage(defaultTriageProtocol, "my husband collapsed and is not responding")
	if cat != TriageRed {
		t.Fatalf("collapsed should triage red, got %s", cat)
	}
	if !strings.Contains(reason, "unconscious") {
		t.Fatalf("reason should name the red flag, got %q", reason)
	}
	cat2, _ := triage(defaultTriageProtocol, "mild headache since morning")
	if cat2 == TriageRed {
		t.Fatalf("headache must not triage red")
	}
}

func TestSOSIntakeCreatesIncident(t *testing.T) {
	s := newTestServer()
	rec, out := post(t, s, "/sos", `{"phone":"+234801","complaint":"heavy bleeding after birth"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("want 201, got %d", rec.Code)
	}
	if out["triage"] != TriageRed {
		t.Fatalf("obstetric bleeding must be red, got %v", out["triage"])
	}
	if out["status"] != StTriaged {
		t.Fatalf("no coords => triaged (awaiting dispatch), got %v", out["status"])
	}
}

func TestRepeatSOSDedupes(t *testing.T) {
	s := newTestServer()
	_, first := post(t, s, "/sos", `{"phone":"+234802","complaint":"chest pain"}`)
	rec, second := post(t, s, "/sos", `{"phone":"+234802","complaint":"chest pain getting worse"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("repeat contact should be 200 update, got %d", rec.Code)
	}
	if first["id"] != second["id"] {
		t.Fatalf("dedupe must keep one incident: %v vs %v", first["id"], second["id"])
	}
	if second["complaint"] != "chest pain getting worse" {
		t.Fatalf("latest description wins, got %v", second["complaint"])
	}
}

func TestTransitionLegality(t *testing.T) {
	s := newTestServer()
	_, inc := post(t, s, "/sos", `{"phone":"+234803","complaint":"convulsion"}`)
	id := inc["id"].(string)
	rec, _ := post(t, s, "/incidents/"+id+"/transition", `{"to":"closed","actor":"d1"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("triaged->closed must be illegal, got %d", rec.Code)
	}
	rec, _ = post(t, s, "/incidents/"+id+"/transition", `{"to":"dispatched","actor":"d1"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("triaged->dispatched legal, got %d", rec.Code)
	}
}

func TestWhatsAppVerifyAndInbound(t *testing.T) {
	s := newTestServer()
	t.Setenv("WHATSAPP_VERIFY_TOKEN", "tok123")
	req := httptest.NewRequest(http.MethodGet,
		"/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=tok123&hub.challenge=ch-42", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if rec.Body.String() != "ch-42" {
		t.Fatalf("verify must echo challenge, got %q", rec.Body.String())
	}

	body := `{"entry":[{"changes":[{"value":{"messages":[
		{"from":"+234804","type":"text","text":{"body":"severe breathing difficulty"}},
		{"from":"+234804","type":"location","location":{"latitude":6.5244,"longitude":3.3792}}
	]}}]}]}`
	rec2, out := post(t, s, "/webhooks/whatsapp", body)
	if rec2.Code != http.StatusOK {
		t.Fatalf("webhook always 200, got %d", rec2.Code)
	}
	if out["incidents_created"].(float64) != 1 {
		t.Fatalf("text creates 1 incident; location dedupes onto it, got %v", out)
	}
	incidents := s.store.List("", "")
	if len(incidents) != 1 {
		t.Fatalf("whatsapp text+location from same caller must dedupe to 1 incident, got %d", len(incidents))
	}
	if incidents[0].LocSource != "whatsapp-pin" {
		t.Fatalf("location message should update coords with whatsapp-pin source, got %q", incidents[0].LocSource)
	}
}

func TestWhatsAppMalformedStill200(t *testing.T) {
	s := newTestServer()
	rec, _ := post(t, s, "/webhooks/whatsapp", `{"not":"whatsapp"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("malformed webhook must still 200 (no retry loops), got %d", rec.Code)
	}
}

func TestValidationErrors(t *testing.T) {
	s := newTestServer()
	rec, _ := post(t, s, "/sos", `{"phone":"+234805"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("missing complaint must 400, got %d", rec.Code)
	}
	rec, _ = post(t, s, "/sos", `{"complaint":"help"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("missing phone+patient_id must 400, got %d", rec.Code)
	}
}

func TestCORSPreflightAndHeaders(t *testing.T) {
	s := newTestServer()
	h := cors(s.routes())
	// preflight
	req := httptest.NewRequest("OPTIONS", "/sos", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusNoContent ||
		rec.Header().Get("Access-Control-Allow-Origin") != "*" {
		t.Fatalf("preflight: %d %v", rec.Code, rec.Header())
	}
	// real request carries the header
	req = httptest.NewRequest("GET", "/healthz", nil)
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Header().Get("Access-Control-Allow-Origin") != "*" {
		t.Fatal("responses carry the CORS header")
	}
}
