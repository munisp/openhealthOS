package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestMCILifecycle(t *testing.T) {
	s := newTestServer()
	rec, out := post(t, s, "/mci", `{"name":"Expressway bus crash","lat":6.5244,"lng":3.3792}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("create mci: %d", rec.Code)
	}
	id := out["id"].(string)

	// triage the scene
	for _, c := range []string{
		`{"tag":"red","description":"adult, flail chest"}`,
		`{"tag":"red","age_group":"child","description":"child, head injury"}`,
		`{"tag":"yellow","description":"adult, femur fracture"}`,
		`{"tag":"green","description":"walking wounded"}`,
		`{"tag":"black","description":"no signs of life"}`,
	} {
		rec, _ = post(t, s, "/mci/"+id+"/casualties", c)
		if rec.Code != http.StatusCreated {
			t.Fatalf("add casualty: %d", rec.Code)
		}
	}

	req := httptest.NewRequest(http.MethodGet, "/mci/"+id, nil)
	rec2 := httptest.NewRecorder()
	s.routes().ServeHTTP(rec2, req)
	var view map[string]any
	if err := json.Unmarshal(rec2.Body.Bytes(), &view); err != nil {
		t.Fatal(err)
	}
	counts := view["counts"].(map[string]any)
	if counts["red"] != 2.0 || counts["total"] != 5.0 || counts["at_scene"] != 5.0 {
		t.Fatalf("counts: %v", counts)
	}

	// transport a red casualty to a facility
	m := view["mci"].(map[string]any)
	cas := m["casualties"].([]any)[0].(map[string]any)
	rec, _ = post(t, s, "/mci/"+id+"/casualties/"+cas["id"].(string),
		`{"status":"transported","facility_id":"HOSP-EMG"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("transport: %d", rec.Code)
	}
	req = httptest.NewRequest(http.MethodGet, "/mci/"+id, nil)
	rec3 := httptest.NewRecorder()
	s.routes().ServeHTTP(rec3, req)
	_ = json.Unmarshal(rec3.Body.Bytes(), &view)
	counts = view["counts"].(map[string]any)
	if counts["at_scene"] != 4.0 {
		t.Fatalf("one transported => 4 at scene, got %v", counts["at_scene"])
	}
}

func TestMCIValidation(t *testing.T) {
	s := newTestServer()
	rec, out := post(t, s, "/mci", `{"name":"X","lat":1,"lng":1}`)
	id := out["id"].(string)
	_ = rec
	rec, _ = post(t, s, "/mci/"+id+"/casualties", `{"tag":"purple"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("invalid START tag must 400, got %d", rec.Code)
	}
	rec, _ = post(t, s, "/mci", `{"lat":1,"lng":1}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("nameless MCI must 400, got %d", rec.Code)
	}
}

func TestVoiceIVRSeam(t *testing.T) {
	s := newTestServer()
	// new call greets and asks for one key
	rec, out := post(t, s, "/webhooks/voice", `{"from":"+2348030009999","call_id":"c1"}`)
	if rec.Code != http.StatusOK || out["expect_dtmf"] != true {
		t.Fatalf("greeting: %d %v", rec.Code, out)
	}
	if !strings.Contains(out["play"].(string), "Press 1") {
		t.Fatalf("greeting names the one key: %v", out)
	}
	// DTMF 1 becomes an SOS on the voice channel
	rec, out = post(t, s, "/webhooks/voice",
		`{"from":"+2348030009999","dtmf":"1","call_id":"c1","lat":6.52,"lng":3.38}`)
	if rec.Code != http.StatusOK || out["incident_id"] == nil {
		t.Fatalf("dtmf 1 creates an incident: %d %v", rec.Code, out)
	}
	inc := s.store.List("", "")[0]
	if inc.Channel != "voice" || inc.LocSource != "cell-tower" {
		t.Fatalf("voice intake: %+v", inc)
	}
	// repeat call dedupes with reassurance, not a clone
	_, out = post(t, s, "/webhooks/voice", `{"from":"+2348030009999","dtmf":"1","call_id":"c2"}`)
	if !strings.Contains(out["play"].(string), "already have") {
		t.Fatalf("repeat call reassures: %v", out)
	}
	if len(s.store.List("", "")) != 1 {
		t.Fatal("no clone from a repeat call")
	}
	// malformed still 200 — same no-retry doctrine as WhatsApp
	rec, _ = post(t, s, "/webhooks/voice", `{"not":"a call"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("malformed voice event must 200, got %d", rec.Code)
	}
}
