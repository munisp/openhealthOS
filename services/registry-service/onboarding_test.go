package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func obServer() server {
	return server{store: NewMemoryStore(), onboarding: newOnboardingStore()}
}

func obPost(t *testing.T, s server, path, body string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var out map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &out)
	return rec, out
}

func TestRoleCatalogueServed(t *testing.T) {
	s := obServer()
	req := httptest.NewRequest(http.MethodGet, "/onboarding/roles", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var out struct {
		Roles map[string][]string `json:"roles"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	if len(out.Roles) < 10 {
		t.Fatalf("role catalogue should cover the platform, got %d", len(out.Roles))
	}
	if len(out.Roles["physician"]) != 5 {
		t.Fatalf("physician walks all five steps, got %v", out.Roles["physician"])
	}
	for _, step := range out.Roles["dispatcher"] {
		if step == StepCredentialVerify {
			t.Fatalf("dispatcher has no statutory licence step: %v", out.Roles["dispatcher"])
		}
	}
}

func TestPhysicianFullChain(t *testing.T) {
	s := obServer()
	rec, ob := obPost(t, s, "/onboarding",
		`{"role":"physician","name":"Dr. Aisha","phone":"+234701","actor":"admin-1"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("invite: %d", rec.Code)
	}
	id := ob["id"].(string)
	if ob["current_step"] != StepIdentityCheck {
		t.Fatalf("after invite the chain waits on identity, got %v", ob["current_step"])
	}
	steps := []struct{ body, wantNext string }{
		{`{"actor":"admin-1","detail":"NIN verified"}`, StepCredentialVerify},
		{`{"actor":"admin-1","license":"MDCN-12345","detail":"MDCN verified"}`, StepFacilityAssign},
		{`{"actor":"admin-1","facility_id":"PHC-1","detail":"posted to PHC-1"}`, StepActivate},
		{`{"actor":"admin-1","detail":"account activated"}`, ""},
	}
	for _, st := range steps {
		rec, ob = obPost(t, s, "/onboarding/"+id+"/advance", st.body)
		if rec.Code != http.StatusOK {
			t.Fatalf("advance: %d %v", rec.Code, ob)
		}
		if ob["current_step"] != nil && ob["current_step"] != st.wantNext {
			t.Fatalf("next step: got %v want %q", ob["current_step"], st.wantNext)
		}
	}
	if ob["status"] != "active" {
		t.Fatalf("chain complete => active, got %v", ob["status"])
	}
	if ob["license"] != "MDCN-12345" || ob["facility_id"] != "PHC-1" {
		t.Fatalf("licence/facility not captured: %v", ob)
	}
	// active is terminal for advance
	rec, _ = obPost(t, s, "/onboarding/"+id+"/advance", `{"actor":"admin-1"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("advancing an active account must 400, got %d", rec.Code)
	}
}

func TestDispatcherSkipsCredentialVerify(t *testing.T) {
	s := obServer()
	_, ob := obPost(t, s, "/onboarding",
		`{"role":"dispatcher","name":"Musa","phone":"+234702","actor":"admin-1"}`)
	id := ob["id"].(string)
	rec, ob := obPost(t, s, "/onboarding/"+id+"/advance", `{"actor":"admin-1","detail":"NIN ok"}`)
	if ob["current_step"] != StepFacilityAssign {
		t.Fatalf("dispatcher: identity -> facility-assign directly, got %v", ob["current_step"])
	}
	_ = rec
}

func TestUnknownRoleAndAnonymousRefused(t *testing.T) {
	s := obServer()
	rec, _ := obPost(t, s, "/onboarding", `{"role":"superuser","name":"X","phone":"+2347","actor":"a"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("unknown role must 400, got %d", rec.Code)
	}
	rec, _ = obPost(t, s, "/onboarding", `{"role":"nurse","name":"X","phone":"+2347"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("anonymous invite must 400 (ADR-0018), got %d", rec.Code)
	}
}

func TestSuspensionNamesWhoAndWhy(t *testing.T) {
	s := obServer()
	_, ob := obPost(t, s, "/onboarding",
		`{"role":"nurse","name":"Ngozi","phone":"+234703","actor":"admin-1"}`)
	id := ob["id"].(string)
	rec, _ := obPost(t, s, "/onboarding/"+id+"/status", `{"status":"suspended","actor":"admin-1"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("suspension without a reason must 400, got %d", rec.Code)
	}
	rec, ob = obPost(t, s, "/onboarding/"+id+"/status",
		`{"status":"suspended","actor":"admin-1","reason":"licence under review"}`)
	if rec.Code != http.StatusOK || ob["status"] != "suspended" {
		t.Fatalf("suspension: %d %v", rec.Code, ob)
	}
	// the record survives suspension — it is a state, not a deletion
	req := httptest.NewRequest(http.MethodGet, "/onboarding/"+id, nil)
	rec2 := httptest.NewRecorder()
	s.routes().ServeHTTP(rec2, req)
	if rec2.Code != http.StatusOK {
		t.Fatalf("suspended record must still read, got %d", rec2.Code)
	}
}
