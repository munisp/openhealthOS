package main

// Portal self-service rail tests (ADR-0017): gate behaviour, proxy
// fidelity (method, path, body), the consent-ownership check, and
// honest 502s when a leg is down.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func portalActionsRig(t *testing.T) (*Service, *http.ServeMux) {
	t.Helper()
	store := NewMemoryStore()
	if err := store.PutSubscription(Subscription{
		PatientID: "P-1", Phone: NormalizePhone("08031234567"), Language: "en", Facility: "F-1",
	}); err != nil {
		t.Fatal(err)
	}
	svc := NewService(store, &LogProvider{})
	mux := http.NewServeMux()
	registerPortalActions(mux, svc)
	return svc, mux
}

type capturedCall struct {
	Method, Path, Body string
}

// upstream spins an httptest server that records each call and replies
// with the given payload.
func upstream(t *testing.T, calls *[]capturedCall, reply string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var sb strings.Builder
		buf := make([]byte, 4096)
		for {
			n, err := r.Body.Read(buf)
			sb.Write(buf[:n])
			if err != nil {
				break
			}
		}
		*calls = append(*calls, capturedCall{r.Method, r.URL.RequestURI(), sb.String()})
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(reply))
	}))
	t.Cleanup(srv.Close)
	return srv
}

func doReq(t *testing.T, mux *http.ServeMux, method, path, body string) *httptest.ResponseRecorder {
	t.Helper()
	var rdr *strings.Reader
	if body != "" {
		rdr = strings.NewReader(body)
	} else {
		rdr = strings.NewReader("")
	}
	req := httptest.NewRequest(method, path, rdr)
	rr := httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	return rr
}

func TestPortalCoverageProxy(t *testing.T) {
	var calls []capturedCall
	srv := upstream(t, &calls, `{"eligible":true,"reason":"","benefit_services":7}`)
	t.Setenv("ELIGIBILITY_URL", srv.URL)
	_, mux := portalActionsRig(t)

	rr := doReq(t, mux, "GET", "/portal/coverage?patient_id=P-1&phone=08031234567", "")
	if rr.Code != 200 || !strings.Contains(rr.Body.String(), `"eligible":true`) {
		t.Fatalf("coverage: %d %s", rr.Code, rr.Body)
	}
	if len(calls) != 1 || calls[0].Method != "POST" || calls[0].Path != "/eligibility" {
		t.Fatalf("upstream call: %+v", calls)
	}
	if !strings.Contains(calls[0].Body, `"member_id":"P-1"`) {
		t.Fatalf("member id forwarded: %s", calls[0].Body)
	}
	// Wrong phone → 404, no upstream call.
	rr = doReq(t, mux, "GET", "/portal/coverage?patient_id=P-1&phone=08099999999", "")
	if rr.Code != http.StatusNotFound || len(calls) != 1 {
		t.Fatalf("gate: %d calls=%d", rr.Code, len(calls))
	}
}

func TestPortalAppointmentsBookAndCancel(t *testing.T) {
	var calls []capturedCall
	srv := upstream(t, &calls, `{"id":"APT-0001","status":"booked"}`)
	t.Setenv("SCHEDULING_URL", srv.URL)
	_, mux := portalActionsRig(t)

	rr := doReq(t, mux, "POST", "/portal/appointments",
		`{"patient_id":"P-1","phone":"08031234567","slot_id":"SCH-1:2026-10-19:480"}`)
	if rr.Code != 200 {
		t.Fatalf("book: %d %s", rr.Code, rr.Body)
	}
	if len(calls) != 1 || calls[0].Path != "/appointments" {
		t.Fatalf("book upstream: %+v", calls)
	}
	// member_id defaults to the patient id.
	if !strings.Contains(calls[0].Body, `"member_id":"P-1"`) ||
		!strings.Contains(calls[0].Body, `"slot_id":"SCH-1:2026-10-19:480"`) {
		t.Fatalf("book body: %s", calls[0].Body)
	}
	// Cancel routes to the appointment-scoped path.
	rr = doReq(t, mux, "POST", "/portal/appointments/cancel",
		`{"patient_id":"P-1","phone":"08031234567","appointment_id":"APT-0001"}`)
	if rr.Code != 200 || calls[1].Path != "/appointments/APT-0001/cancel" {
		t.Fatalf("cancel: %d %+v", rr.Code, calls[1])
	}
	// List mine.
	rr = doReq(t, mux, "GET", "/portal/appointments?patient_id=P-1&phone=08031234567", "")
	if rr.Code != 200 || calls[2].Path != "/appointments?patient_id=P-1" {
		t.Fatalf("list: %d %+v", rr.Code, calls[2])
	}
	// Slots browse passes the facility/service/date triple through.
	rr = doReq(t, mux, "GET", "/portal/slots?patient_id=P-1&phone=08031234567&facility_id=F-1&service=anc&date=2026-10-19", "")
	if rr.Code != 200 || !strings.Contains(calls[3].Path, "/slots?facility_id=F-1&service=anc&date=2026-10-19") {
		t.Fatalf("slots: %d %+v", rr.Code, calls[3])
	}
}

func TestPortalMessagesOpensTriageConsult(t *testing.T) {
	var calls []capturedCall
	srv := upstream(t, &calls, `{"id":"CONS-0007","status":"open"}`)
	t.Setenv("TELEHEALTH_URL", srv.URL)
	_, mux := portalActionsRig(t)

	rr := doReq(t, mux, "POST", "/portal/messages",
		`{"patient_id":"P-1","phone":"08031234567","facility_id":"F-1","complaint":"fever for two days"}`)
	if rr.Code != 200 {
		t.Fatalf("message: %d %s", rr.Code, rr.Body)
	}
	if len(calls) != 1 || calls[0].Path != "/consults" {
		t.Fatalf("consult upstream: %+v", calls)
	}
	if !strings.Contains(calls[0].Body, `"nurse_id":"portal-triage"`) ||
		!strings.Contains(calls[0].Body, "fever for two days") {
		t.Fatalf("consult body: %s", calls[0].Body)
	}
	// Empty complaint → 400 before the gate.
	rr = doReq(t, mux, "POST", "/portal/messages",
		`{"patient_id":"P-1","phone":"08031234567","complaint":""}`)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("empty complaint: %d", rr.Code)
	}
}

func TestPortalConsentsOwnership(t *testing.T) {
	var calls []capturedCall
	srv := upstream(t, &calls, `{"consents":[{"id":"CNS-1"},{"id":"CNS-2"}]}`)
	t.Setenv("CONSENT_URL", srv.URL)
	_, mux := portalActionsRig(t)

	// List.
	rr := doReq(t, mux, "GET", "/portal/consents?patient_id=P-1&phone=08031234567", "")
	if rr.Code != 200 || !strings.Contains(rr.Body.String(), "CNS-1") {
		t.Fatalf("list: %d %s", rr.Code, rr.Body)
	}
	// Revoke an owned consent → proxied.
	rr = doReq(t, mux, "POST", "/portal/consents/revoke",
		`{"patient_id":"P-1","phone":"08031234567","consent_id":"CNS-2"}`)
	if rr.Code != 200 {
		t.Fatalf("revoke owned: %d %s", rr.Code, rr.Body)
	}
	last := calls[len(calls)-1]
	if last.Path != "/consents/CNS-2/revoke" {
		t.Fatalf("revoke path: %+v", last)
	}
	// Revoke a consent NOT in the patient's list → 404, never proxied.
	before := len(calls)
	rr = doReq(t, mux, "POST", "/portal/consents/revoke",
		`{"patient_id":"P-1","phone":"08031234567","consent_id":"CNS-99"}`)
	if rr.Code != http.StatusNotFound {
		t.Fatalf("revoke foreign: %d", rr.Code)
	}
	for _, c := range calls[before:] {
		if strings.Contains(c.Path, "revoke") {
			t.Fatalf("foreign consent must never reach revoke: %+v", c)
		}
	}
}

func TestPortalUpstreamDownIs502(t *testing.T) {
	t.Setenv("ELIGIBILITY_URL", "http://127.0.0.1:9")
	_, mux := portalActionsRig(t)
	rr := doReq(t, mux, "GET", "/portal/coverage?patient_id=P-1&phone=08031234567", "")
	if rr.Code != http.StatusBadGateway {
		t.Fatalf("dead upstream must be 502, got %d", rr.Code)
	}
	var body map[string]any
	json.NewDecoder(rr.Body).Decode(&body)
	if body["error"] == nil {
		t.Fatalf("502 carries a plain error: %s", rr.Body)
	}
}
