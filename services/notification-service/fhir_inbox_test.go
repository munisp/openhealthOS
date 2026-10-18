package main

// FHIR inbox tests: Medplum-style resources become patient notifications,
// clinical detail never crosses into the message, non-lab observations
// and unknown resourceTypes are ignored.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestFHIRInboxLabObservation(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	_ = svc.store.PutSubscription(Subscription{PatientID: "PT-1", Phone: "+234801", Facility: "PHC-Kawo"})
	mux := http.NewServeMux()
	registerFHIRInbox(mux, svc)

	obs := `{"resourceType":"Observation","category":[{"coding":[{"code":"laboratory"}]}],
		"code":{"text":"Malaria RDT"},"subject":{"reference":"Patient/PT-1"}}`
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/fhir/inbox", strings.NewReader(obs)))
	if rec.Code != 201 {
		t.Fatalf("lab observation: %d %s", rec.Code, rec.Body)
	}
	var n Notification
	json.NewDecoder(rec.Body).Decode(&n)
	if n.Kind != "lab_ready" || n.PatientID != "PT-1" {
		t.Fatalf("mapping: %+v", n)
	}
	if !strings.Contains(n.Text, "Malaria RDT") || strings.Contains(n.Text, "positive") {
		t.Fatalf("message carries the test name but never the result: %q", n.Text)
	}
}

func TestFHIRInboxIgnoresVitalSigns(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	mux := http.NewServeMux()
	registerFHIRInbox(mux, svc)
	vital := `{"resourceType":"Observation","category":[{"coding":[{"code":"vital-signs"}]}],
		"code":{"text":"SpO2"},"subject":{"reference":"Patient/PT-1"}}`
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/fhir/inbox", strings.NewReader(vital)))
	if rec.Code != 202 {
		t.Fatalf("vital signs must be ignored (202), got %d", rec.Code)
	}
}

func TestFHIRInboxMedicationRequest(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	_ = svc.store.PutSubscription(Subscription{PatientID: "PT-2", Phone: "+234802", Facility: "PHC-1"})
	mux := http.NewServeMux()
	registerFHIRInbox(mux, svc)
	mr := `{"resourceType":"MedicationRequest","medicationCodeableConcept":{"text":"Amoxicillin 500mg"},
		"subject":{"reference":"Patient/PT-2"}}`
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/fhir/inbox", strings.NewReader(mr)))
	if rec.Code != 201 {
		t.Fatalf("medication request: %d %s", rec.Code, rec.Body)
	}
	var n Notification
	json.NewDecoder(rec.Body).Decode(&n)
	if n.Kind != "pickup" || !strings.Contains(n.Text, "Amoxicillin") {
		t.Fatalf("mapping: %+v", n)
	}
}

func TestFHIRInboxAppointment(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	_ = svc.store.PutSubscription(Subscription{PatientID: "PT-3", Phone: "+234803", Facility: "PHC-1"})
	mux := http.NewServeMux()
	registerFHIRInbox(mux, svc)
	appt := `{"resourceType":"Appointment","start":"2026-10-20T09:00:00Z",
		"participant":[{"actor":{"reference":"Patient/PT-3"}},{"actor":{"reference":"Practitioner/N-1"}}]}`
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/fhir/inbox", strings.NewReader(appt)))
	if rec.Code != 201 {
		t.Fatalf("appointment: %d %s", rec.Code, rec.Body)
	}
	var n Notification
	json.NewDecoder(rec.Body).Decode(&n)
	if n.Kind != "appointment" || !strings.Contains(n.Text, "2026-10-20") {
		t.Fatalf("mapping: %+v", n)
	}
}

func TestFHIRInboxNoSubscriptionSkips(t *testing.T) {
	svc := NewService(NewMemoryStore(), &LogProvider{})
	mux := http.NewServeMux()
	registerFHIRInbox(mux, svc)
	mr := `{"resourceType":"MedicationRequest","subject":{"reference":"Patient/NOPE"}}`
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodPost, "/fhir/inbox", strings.NewReader(mr)))
	if rec.Code != 202 {
		t.Fatalf("unsubscribed patient must skip (202), got %d", rec.Code)
	}
	if got := len(svc.store.Pending(10)); got != 0 {
		t.Fatalf("nothing enqueued, got %d", got)
	}
}
