package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
)

type stubConsent struct{ allow bool; err error }

func (c stubConsent) Check(patientID, grantee, scope string) (bool, error) {
	return c.allow, c.err
}

func fedSetup(t *testing.T, c ConsentChecker) (*federationHandler, *http.ServeMux) {
	st := NewStore(stubConsent{allow: true})
	h := newFederationHandler(st, c, "OHF-LG-IKE-0001", map[string]string{"OHF-LG-IKE-0002": "http://peer"})
	mux := http.NewServeMux()
	h.routes(mux)
	return h, mux
}

func TestBundleRequiresConsent(t *testing.T) {
	_, mux := fedSetup(t, stubConsent{allow: false})
	body, _ := json.Marshal(map[string]string{"patient_id": "P1", "target_facility": "OHF-LG-IKE-0002"})
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("POST", "/federation/bundle", bytes.NewReader(body)))
	if w.Code != 403 {
		t.Fatalf("no consent must fail closed, got %d", w.Code)
	}
}

func TestBundleConsentServiceDown(t *testing.T) {
	_, mux := fedSetup(t, stubConsent{err: errors.New("down")})
	body, _ := json.Marshal(map[string]string{"patient_id": "P1", "target_facility": "OHF-LG-IKE-0002"})
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("POST", "/federation/bundle", bytes.NewReader(body)))
	if w.Code != 403 {
		t.Fatalf("consent down must fail closed, got %d", w.Code)
	}
}

func TestBundleWithConsent(t *testing.T) {
	h, mux := fedSetup(t, stubConsent{allow: true})
	h.store.Put(Resource{ResourceType: "Patient", ID: "P1", PatientID: "P1", SourceFacility: "OHF-LG-IKE-0001", Body: json.RawMessage(`{"resourceType":"Patient","id":"P1"}`)})
	h.store.Put(Resource{ResourceType: "Observation", ID: "O1", PatientID: "P1", SourceFacility: "OHF-LG-IKE-0001", Body: json.RawMessage(`{"resourceType":"Observation","id":"O1"}`)})
	body, _ := json.Marshal(map[string]string{"patient_id": "P1", "target_facility": "OHF-LG-IKE-0002"})
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("POST", "/federation/bundle", bytes.NewReader(body)))
	if w.Code != 200 {
		t.Fatalf("got %d %s", w.Code, w.Body)
	}
	var out map[string]any
	json.Unmarshal(w.Body.Bytes(), &out)
	if out["type"] != "transaction" {
		t.Fatalf("got %v", out["type"])
	}
}

func TestUnknownPeer(t *testing.T) {
	_, mux := fedSetup(t, stubConsent{allow: true})
	body, _ := json.Marshal(map[string]string{"patient_id": "P1", "target_facility": "NOPE"})
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("POST", "/federation/bundle", bytes.NewReader(body)))
	if w.Code != 404 {
		t.Fatalf("want 404, got %d", w.Code)
	}
}

func TestPeersManifest(t *testing.T) {
	_, mux := fedSetup(t, stubConsent{})
	w := httptest.NewRecorder()
	mux.ServeHTTP(w, httptest.NewRequest("GET", "/federation/peers", nil))
	var out map[string]any
	json.Unmarshal(w.Body.Bytes(), &out)
	if out["self"] != "OHF-LG-IKE-0001" || len(out["peers"].([]any)) != 1 {
		t.Fatalf("got %v", out)
	}
}
