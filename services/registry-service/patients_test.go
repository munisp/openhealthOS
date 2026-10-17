package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestOfflineIDIssuance(t *testing.T) {
	ps := NewMemoryPatientStore()
	p1, err := ps.Register(Patient{Name: "Amina Yusuf", FacilityID: "PHC-007"})
	if err != nil {
		t.Fatal(err)
	}
	p2, _ := ps.Register(Patient{Name: "Chinedu Okafor", FacilityID: "PHC-007"})
	if p1.ID != "OH-PHC-007-000001" || p2.ID != "OH-PHC-007-000002" {
		t.Fatalf("offline ids not sequential per facility: %s %s", p1.ID, p2.ID)
	}
	// another facility, independent sequence — no coordination needed
	p3, _ := ps.Register(Patient{Name: "Bola Adeyemi", FacilityID: "PHC-012"})
	if p3.ID != "OH-PHC-012-000001" {
		t.Fatalf("facility sequence not independent: %s", p3.ID)
	}
}

func TestNINBindingIsProgressiveAndUnique(t *testing.T) {
	ps := NewMemoryPatientStore()
	p, _ := ps.Register(Patient{Name: "Amina", FacilityID: "PHC-007"})
	linked, err := ps.LinkNIN(p.ID, "12345678901")
	if err != nil || linked.NIN != "12345678901" {
		t.Fatalf("link failed: %v", err)
	}
	if _, err := ps.LinkNIN(p.ID, "99999999999"); err != ErrNINBound {
		t.Fatal("re-binding must be rejected")
	}
	p2, _ := ps.Register(Patient{Name: "Chinedu", FacilityID: "PHC-007"})
	if _, err := ps.LinkNIN(p2.ID, "12345678901"); err == nil {
		t.Fatal("NIN must be unique across the registry")
	}
	found, err := ps.FindByNIN("12345678901")
	if err != nil || found.ID != p.ID {
		t.Fatal("NIN lookup failed")
	}
}

func TestFHIRPatientProfileShape(t *testing.T) {
	ps := NewMemoryPatientStore()
	p, _ := ps.Register(Patient{Name: "Amina", FacilityID: "PHC-007", DOB: "1992-04-15", Phone: "+234801"})
	ps.LinkNIN(p.ID, "12345678901")
	f := FHIRPatient(mustGet(t, ps, p.ID))
	if f["resourceType"] != "Patient" {
		t.Fatalf("not a Patient: %v", f)
	}
	ids := f["identifier"].([]map[string]any)
	if len(ids) != 2 {
		t.Fatal("must carry both NIN and offline id identifiers")
	}
	systems := map[string]bool{}
	for _, id := range ids {
		systems[id["system"].(string)] = true
	}
	if !systems["https://nimc.gov.ng/fhir/NamingSystem/nin"] ||
		!systems["https://openhealthos.org/fhir/NamingSystem/offline-id"] {
		t.Fatalf("identifier systems wrong: %v", ids)
	}
}

func mustGet(t *testing.T, ps PatientStore, id string) Patient {
	t.Helper()
	p, err := ps.GetPatient(id)
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestPatientHTTPLifecycle(t *testing.T) {
	ps := NewMemoryPatientStore()
	mux := http.NewServeMux()
	registerPatientRoutes(mux, ps)

	req := httptest.NewRequest(http.MethodPost, "/patients",
		strings.NewReader(`{"name":"Amina Yusuf","facility_id":"PHC-007","dob":"1992-04-15"}`))
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != 201 {
		t.Fatalf("register: %d %s", rec.Code, rec.Body)
	}
	var p Patient
	json.Unmarshal(rec.Body.Bytes(), &p)

	req = httptest.NewRequest(http.MethodPost, "/patients/"+p.ID+"/nin",
		strings.NewReader(`{"nin":"12345678901"}`))
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("link nin: %d", rec.Code)
	}

	req = httptest.NewRequest(http.MethodGet, "/fhir/Patient/"+p.ID, nil)
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if !strings.Contains(rec.Body.String(), `"resourceType":"Patient"`) {
		t.Fatalf("FHIR projection missing: %s", rec.Body)
	}
}
