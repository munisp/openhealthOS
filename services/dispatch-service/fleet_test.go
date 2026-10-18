package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func registerUnit(t *testing.T, s server, callsign, facility string, lat, lng float64) Unit {
	t.Helper()
	rec, out := post(t, s, "/units", `{"callsign":"`+callsign+`","facility_id":"`+facility+`"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("register unit: %d", rec.Code)
	}
	id := out["id"].(string)
	rec, _ = post(t, s, "/units/"+id+"/position",
		`{"lat":`+ftoa(lat)+`,"lng":`+ftoa(lng)+`}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("ping: %d", rec.Code)
	}
	u, _ := s.fleet.Get(id)
	return u
}

func ftoa(f float64) string {
	b, _ := json.Marshal(f)
	return string(b)
}

func TestAutoDispatchNearestUnit(t *testing.T) {
	s := newTestServer()
	far := registerUnit(t, s, "AMB-FAR", "HOSP-EMG", 6.60, 3.40)
	near := registerUnit(t, s, "AMB-NEAR", "HOSP-EMG", 6.525, 3.380)
	if far.ID == near.ID {
		t.Fatal("unit ids must differ")
	}
	// SOS with coordinates but unreachable registry: triaged, not yet
	// dispatched — no unit assigned.
	sosIncident(t, s, "+234911", "unconscious and not responding")
	inc := s.store.List("", "")[0]
	if inc.UnitID != "" {
		t.Fatalf("no unit until the incident is dispatched, got %s", inc.UnitID)
	}
	// Dispatcher assigns the facility (bumps to dispatched), then a unit.
	rec, _ := post(t, s, "/incidents/"+inc.ID+"/assign",
		`{"facility_id":"HOSP-EMG","facility_name":"General Hospital","actor":"d1"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("assign: %d", rec.Code)
	}
	rec, out := post(t, s, "/incidents/"+inc.ID+"/dispatch-unit", `{"actor":"d1"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("dispatch-unit: %d", rec.Code)
	}
	if out["unit_id"] != near.ID {
		t.Fatalf("nearest unit wins: got %v want %s", out["unit_id"], near.ID)
	}
	u, _ := s.fleet.Get(near.ID)
	if u.Status != UnitEnRoute || u.IncidentID != inc.ID {
		t.Fatalf("unit must be en_route on the incident: %+v", u)
	}
}

func TestTrackEndpointFeedsTheMap(t *testing.T) {
	s := newTestServer()
	s.ix.now = func() time.Time { return time.Date(2026, 2, 1, 10, 0, 0, 0, time.UTC) }
	u := registerUnit(t, s, "AMB-7", "HOSP-EMG", 6.5300, 3.3792)
	inc := sosIncident(t, s, "+234912", "severe breathing difficulty")
	id := inc["id"].(string)
	post(t, s, "/incidents/"+id+"/assign",
		`{"facility_id":"HOSP-EMG","facility_name":"General Hospital","actor":"d1"}`)
	// patient at ~6.5244,3.3792? SOS had no coords — give it coords first
	sos := `{"phone":"+234912","complaint":"severe breathing difficulty","lat":6.5244,"lng":3.3792}`
	post(t, s, "/sos", sos) // dedupe update with coords
	post(t, s, "/incidents/"+id+"/dispatch-unit", `{"actor":"d1"}`)

	// unit moves closer between pings
	post(t, s, "/units/"+u.ID+"/position", `{"lat":6.5270,"lng":3.3792}`)

	req := httptest.NewRequest(http.MethodGet, "/incidents/"+id+"/track", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	unit := out["unit"].(map[string]any)
	if unit["callsign"] != "AMB-7" || unit["lat"] != 6.5270 {
		t.Fatalf("unit fix wrong: %v", unit)
	}
	eta := out["eta"].(map[string]any)
	if eta["minutes"].(float64) < 1 {
		t.Fatalf("ETA must be positive: %v", eta)
	}
	if eta["assumptions"] == nil || eta["assumptions"] == "" {
		t.Fatalf("ETA prints its assumptions: %v", eta)
	}
	trail := unit["trail"].([]any)
	if len(trail) != 2 {
		t.Fatalf("trail holds both pings, got %d", len(trail))
	}
	pat := out["patient"].(map[string]any)
	if pat["lat"] != 6.5244 {
		t.Fatalf("patient coords: %v", pat)
	}
}

func TestUnitStatusRidesIncidentLifecycle(t *testing.T) {
	s := newTestServer()
	u := registerUnit(t, s, "AMB-9", "HOSP-EMG", 6.525, 3.380)
	inc := sosIncident(t, s, "+234913", "chest pain and sweating")
	id := inc["id"].(string)
	post(t, s, "/incidents/"+id+"/assign",
		`{"facility_id":"H","facility_name":"H","actor":"d1"}`)
	post(t, s, "/incidents/"+id+"/dispatch-unit", `{"actor":"d1"}`)
	for _, to := range []string{StAcked, StOnScene, StTransported, StClosed} {
		rec, _ := post(t, s, "/incidents/"+id+"/transition", `{"to":"`+to+`","actor":"d1"}`)
		if rec.Code != http.StatusOK {
			t.Fatalf("transition %s: %d", to, rec.Code)
		}
	}
	got, _ := s.fleet.Get(u.ID)
	if got.Status != UnitAvailable || got.IncidentID != "" {
		t.Fatalf("closure returns the unit to available: %+v", got)
	}
}

func TestManualUnitOverrideAndValidation(t *testing.T) {
	s := newTestServer()
	u := registerUnit(t, s, "AMB-1", "H", 6.5, 3.3)
	_ = registerUnit(t, s, "AMB-2", "H", 6.51, 3.31)
	inc := sosIncident(t, s, "+234914", "bleeding")
	id := inc["id"].(string)
	rec, out := post(t, s, "/incidents/"+id+"/dispatch-unit",
		`{"unit_id":"`+u.ID+`","actor":"d1"}`)
	if rec.Code != http.StatusOK || out["unit_id"] != u.ID {
		t.Fatalf("manual override: %d %v", rec.Code, out)
	}
	// auto-assign after the manual override takes the remaining unit…
	u2 := s.fleet.List(UnitAvailable)[0]
	rec, out = post(t, s, "/incidents/"+id+"/dispatch-unit", `{"actor":"d1"}`)
	if rec.Code != http.StatusOK || out["unit_id"] != u2.ID {
		t.Fatalf("auto takes the remaining unit: %d %v", rec.Code, out)
	}
	// …and with the fleet exhausted the answer is an honest 400
	inc2 := sosIncident(t, s, "+234915", "convulsion")
	rec, _ = post(t, s, "/incidents/"+inc2["id"].(string)+"/dispatch-unit", `{"actor":"d1"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("fleet exhausted => 400, got %d", rec.Code)
	}
	rec, _ = post(t, s, "/units/"+u.ID+"/position", `{"lat":0,"lng":0}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("zero-fix ping must 400, got %d", rec.Code)
	}
}

func TestETAMath(t *testing.T) {
	mins, how := ETA(10.0)
	// 10km x 1.4 = 14km at 40km/h = 21min
	if mins != 21 {
		t.Fatalf("ETA(10km) = %d, want 21", mins)
	}
	if how == "" {
		t.Fatal("assumptions printed")
	}
}
