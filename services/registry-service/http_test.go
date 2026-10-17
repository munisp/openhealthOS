package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func seeded() server {
	st := NewMemoryStore()
	st.PutFacility(Facility{
		ID: "PHC-007", Name: "Kawo PHC", State: "Kaduna", LGA: "Kaduna North",
		Ownership: "public", Level: "PHC", Services: []string{"anc", "immunisation"},
		BHCPF: true, Power: "solar", Water: "borehole", Latitude: 10.52, Longitude: 7.44,
	})
	st.PutProvider(Provider{ID: "PRV-100", Name: "Fatima Bello", License: "NMCN-88123", Cadre: "nurse", Facility: "PHC-007", Verified: true})
	return server{store: st}
}

func TestFacilityRoundTrip(t *testing.T) {
	s := seeded()
	req := httptest.NewRequest(http.MethodGet, "/facilities/PHC-007", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("got %d", rec.Code)
	}
	var f Facility
	json.Unmarshal(rec.Body.Bytes(), &f)
	if f.Name != "Kawo PHC" || f.Power != "solar" {
		t.Fatalf("unexpected facility: %+v", f)
	}
}

func TestFacilityNotFound(t *testing.T) {
	s := seeded()
	req := httptest.NewRequest(http.MethodGet, "/facilities/NOPE", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if rec.Code != 404 {
		t.Fatalf("want 404, got %d", rec.Code)
	}
}

func TestFHIRLocationProjection(t *testing.T) {
	s := seeded()
	req := httptest.NewRequest(http.MethodGet, "/fhir/Location/PHC-007", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var loc map[string]any
	json.Unmarshal(rec.Body.Bytes(), &loc)
	if loc["resourceType"] != "Location" || loc["id"] != "PHC-007" {
		t.Fatalf("not a FHIR Location: %v", loc)
	}
	exts, ok := loc["extension"].([]any)
	if !ok || len(exts) != 4 {
		t.Fatalf("infrastructure profile extensions missing: %v", loc["extension"])
	}
	pos, ok := loc["position"].(map[string]any)
	if !ok || pos["latitude"] != 10.52 {
		t.Fatalf("geoposition missing: %v", loc["position"])
	}
}

func TestFHIRPractitionerProjection(t *testing.T) {
	s := seeded()
	req := httptest.NewRequest(http.MethodGet, "/fhir/Practitioner/PRV-100", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var p map[string]any
	json.Unmarshal(rec.Body.Bytes(), &p)
	if p["resourceType"] != "Practitioner" {
		t.Fatalf("not a FHIR Practitioner: %v", p)
	}
}

func TestCreateValidates(t *testing.T) {
	s := seeded()
	req := httptest.NewRequest(http.MethodPost, "/facilities", strings.NewReader(`{"id":""}`))
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	if rec.Code != 400 {
		t.Fatalf("invalid record must be rejected, got %d", rec.Code)
	}
}

func TestListSorted(t *testing.T) {
	s := seeded()
	s.store.PutFacility(Facility{ID: "AAA-1", Name: "A"})
	req := httptest.NewRequest(http.MethodGet, "/facilities", nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var fs []Facility
	json.Unmarshal(rec.Body.Bytes(), &fs)
	if len(fs) != 2 || fs[0].ID != "AAA-1" {
		t.Fatalf("list must be sorted by id: %+v", fs)
	}
}
