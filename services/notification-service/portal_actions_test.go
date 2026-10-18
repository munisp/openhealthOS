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
	srv := upstream(t, &calls, `{"id":"APT-0001","patient_id":"P-1","status":"booked"}`)
	t.Setenv("SCHEDULING_URL", srv.URL)
	svc, mux := portalActionsRig(t)

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
	// Cancel first verifies ownership (GET), then routes to the
	// appointment-scoped path.
	rr = doReq(t, mux, "POST", "/portal/appointments/cancel",
		`{"patient_id":"P-1","phone":"08031234567","appointment_id":"APT-0001"}`)
	if rr.Code != 200 || calls[1].Path != "/appointments/APT-0001" ||
		calls[1].Method != "GET" || calls[2].Path != "/appointments/APT-0001/cancel" {
		t.Fatalf("cancel: %d %+v", rr.Code, calls)
	}
	// Cross-patient cancel: P-2 is a legitimate portal user (gate passes)
	// but the appointment belongs to P-1 — refused after the ownership
	// GET, and the cancel itself never fires.
	svc.store.PutSubscription(Subscription{
		PatientID: "P-2", Phone: NormalizePhone("08031234567"), Language: "en", Facility: "F-1",
	})
	nCalls := len(calls)
	rr = doReq(t, mux, "POST", "/portal/appointments/cancel",
		`{"patient_id":"P-2","phone":"08031234567","appointment_id":"APT-0001"}`)
	if rr.Code != http.StatusNotFound {
		t.Fatalf("cross-patient cancel must 404: %d %s", rr.Code, rr.Body)
	}
	ownershipChecked, cancelFired := false, false
	for _, c := range calls[nCalls:] {
		if c.Method == "GET" && c.Path == "/appointments/APT-0001" {
			ownershipChecked = true
		}
		if strings.HasSuffix(c.Path, "/cancel") {
			cancelFired = true
		}
	}
	if !ownershipChecked || cancelFired {
		t.Fatalf("ownership gate: checked=%v cancelFired=%v calls=%+v", ownershipChecked, cancelFired, calls[nCalls:])
	}
	// List mine.
	rr = doReq(t, mux, "GET", "/portal/appointments?patient_id=P-1&phone=08031234567", "")
	if rr.Code != 200 || calls[len(calls)-1].Path != "/appointments?patient_id=P-1" {
		t.Fatalf("list: %d %+v", rr.Code, calls)
	}
	// Slots browse passes the facility/service/date triple through.
	rr = doReq(t, mux, "GET", "/portal/slots?patient_id=P-1&phone=08031234567&facility_id=F-1&service=anc&date=2026-10-19", "")
	if rr.Code != 200 || !strings.Contains(calls[len(calls)-1].Path, "/slots?facility_id=F-1&service=anc&date=2026-10-19") {
		t.Fatalf("slots: %d %+v", rr.Code, calls)
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

func TestPortalQuestionnaireStampsIdentity(t *testing.T) {
	var calls []capturedCall
	srv := upstream(t, &calls, `{"resourceType":"QuestionnaireResponse"}`)
	t.Setenv("SHR_URL", srv.URL)
	_, mux := portalActionsRig(t)

	rr := doReq(t, mux, "POST", "/portal/questionnaire", `{
		"patient_id":"P-1","phone":"08031234567",
		"response":{"resourceType":"QuestionnaireResponse","questionnaire":"Questionnaire/anc-screening",
			"status":"completed","item":[{"linkId":"bleeding","answer":[{"valueBoolean":true}]}]}}`)
	if rr.Code != 200 {
		t.Fatalf("submit: %d %s", rr.Code, rr.Body)
	}
	if len(calls) != 1 || calls[0].Method != "PUT" {
		t.Fatalf("upstream: %+v", calls)
	}
	if !strings.HasPrefix(calls[0].Path, "/fhir/QuestionnaireResponse/qr-P-1-") {
		t.Fatalf("path: %s", calls[0].Path)
	}
	if !strings.Contains(calls[0].Body, `"Patient/P-1"`) {
		t.Fatalf("subject stamped: %s", calls[0].Body)
	}
	// A non-QuestionnaireResponse is refused before touching the SHR.
	rr = doReq(t, mux, "POST", "/portal/questionnaire",
		`{"patient_id":"P-1","phone":"08031234567","response":{"resourceType":"Observation"}}`)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("wrong type: %d", rr.Code)
	}
}

// ADR-0018: the patient-as-auditor rail — list claims in my name,
// dispute the one that never happened, never touch a neighbour's.
func TestPortalClaimsListAndDispute(t *testing.T) {
	var calls []capturedCall
	// One upstream serving both the for-member list and the dispute.
	listReply := `{"claims":[{"claim":{"claim_id":"C-1","member_id":"P-1"},"decision":{"decision":"APPROVE","payable_ngn":5000},"at":"2026-10-18T10:00:00Z","disputed":false}]}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls = append(calls, capturedCall{r.Method, r.URL.RequestURI(), ""})
		w.Header().Set("Content-Type", "application/json")
		if strings.Contains(r.URL.Path, "/dispute") {
			w.Write([]byte(`{"claim_id":"C-1","disputed":true}`))
			return
		}
		w.Write([]byte(listReply))
	}))
	t.Cleanup(srv.Close)
	t.Setenv("ELIGIBILITY_URL", srv.URL)
	_, mux := portalActionsRig(t)

	// List: gated, proxied.
	rr := doReq(t, mux, "GET", "/portal/claims?patient_id=P-1&phone=08031234567", "")
	if rr.Code != 200 || !strings.Contains(rr.Body.String(), "C-1") {
		t.Fatalf("claims list: %d %s", rr.Code, rr.Body)
	}
	if len(calls) != 1 || calls[0].Path != "/claims/for-member?member_id=P-1" {
		t.Fatalf("upstream list call: %+v", calls)
	}

	// Dispute own claim → ownership check (a for-member GET) then POST.
	rr = doReq(t, mux, "POST", "/portal/claims/dispute",
		`{"patient_id":"P-1","phone":"08031234567","claim_id":"C-1","reason":"I was never at this facility"}`)
	if rr.Code != 200 || !strings.Contains(rr.Body.String(), `"disputed":true`) {
		t.Fatalf("dispute: %d %s", rr.Code, rr.Body)
	}
	if calls[len(calls)-1].Path != "/claims/C-1/dispute" {
		t.Fatalf("upstream dispute call: %+v", calls[len(calls)-1])
	}

	// Dispute someone else's claim id → 404, no dispute POST upstream.
	before := len(calls)
	rr = doReq(t, mux, "POST", "/portal/claims/dispute",
		`{"patient_id":"P-1","phone":"08031234567","claim_id":"C-999","reason":"not mine either"}`)
	if rr.Code != http.StatusNotFound {
		t.Fatalf("cross-member dispute must 404: %d", rr.Code)
	}
	for _, c := range calls[before:] {
		if strings.Contains(c.Path, "/dispute") {
			t.Fatalf("ownership check bypassed: %+v", c)
		}
	}
}
