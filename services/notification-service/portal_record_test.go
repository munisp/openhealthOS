package main

// Portal record tests (ADR-0016): the merged record fan-out — self-access
// header asserted upstream, 404-is-truth, per-leg degradation, consults
// unwrapping, and the subscription auth gate.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// portalRig wires a mux with stub SHR + telehealth upstreams.
func portalRig(t *testing.T, shrHandler, teleHandler http.HandlerFunc) (*Service, *http.ServeMux) {
	t.Helper()
	shr := httptest.NewServer(shrHandler)
	tele := httptest.NewServer(teleHandler)
	t.Cleanup(shr.Close)
	t.Cleanup(tele.Close)
	t.Setenv("SHR_URL", shr.URL)
	t.Setenv("TELEHEALTH_URL", tele.URL)

	svc := NewService(NewMemoryStore(), &LogProvider{})
	svc.stepup.randomPct = 0 // deterministic: existing tests exercise fan-out, not the dice
	if err := svc.store.PutSubscription(Subscription{
		PatientID: "PT-1", Phone: "+2348030000000", Language: "en", Facility: "Kawo PHC",
	}); err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	registerPortalRecord(mux, server{svc: svc})
	return svc, mux
}

// elevate issues+verifies a challenge internally, returning a valid token.
func elevate(t *testing.T, svc *Service, patientID string) string {
	t.Helper()
	code := svc.stepup.issue(patientID)
	tok, ok := svc.stepup.verify(patientID, code)
	if !ok {
		t.Fatal("elevation failed")
	}
	return tok
}

func getRecord(t *testing.T, mux *http.ServeMux, query string) (*httptest.ResponseRecorder, PortalRecord) {
	t.Helper()
	return getRecordTok(t, mux, query, "")
}

func getRecordTok(t *testing.T, mux *http.ServeMux, query, token string) (*httptest.ResponseRecorder, PortalRecord) {
	t.Helper()
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/portal/record?"+query, nil)
	if token != "" {
		req.Header.Set("X-StepUp-Token", token)
	}
	mux.ServeHTTP(rec, req)
	var pr PortalRecord
	if rec.Code == http.StatusOK {
		if err := json.NewDecoder(rec.Body).Decode(&pr); err != nil {
			t.Fatalf("decode: %v", err)
		}
	}
	return rec, pr
}

func TestPortalRecordHappyPath(t *testing.T) {
	var gotSelf, gotPath string
	svc, mux := portalRig(t,
		func(w http.ResponseWriter, r *http.Request) {
			gotSelf = r.Header.Get("X-Patient-Self")
			gotPath = r.URL.Path
			w.Write([]byte(`{"resourceType":"Bundle","type":"searchset","total":1,
				"entry":[{"resource":{"resourceType":"Observation","id":"obs-1"}}]}`))
		},
		func(w http.ResponseWriter, r *http.Request) {
			w.Write([]byte(`{"consults":[{"id":"c-1","complaint":"fever","status":"closed"}]}`))
		})

	rec, pr := getRecordTok(t, mux, "patient_id=PT-1&phone=%2B2348030000000", elevate(t, svc, "PT-1"))
	if rec.Code != http.StatusOK {
		t.Fatalf("status: %d %s", rec.Code, rec.Body)
	}
	// The self-access basis is asserted to the SHR and names the patient.
	if gotSelf != "PT-1" || gotPath != "/fhir/Patient/PT-1/$everything" {
		t.Fatalf("upstream call: self=%q path=%q", gotSelf, gotPath)
	}
	if !pr.Complete {
		t.Fatalf("expected complete record: %+v", pr)
	}
	if !strings.Contains(string(pr.Record), `"obs-1"`) {
		t.Fatalf("record leg missing: %s", pr.Record)
	}
	// Consults arrive as a bare array, not the {"consults": ...} wrapper.
	var consults []map[string]any
	if err := json.Unmarshal(pr.Consults, &consults); err != nil || len(consults) != 1 {
		t.Fatalf("consults unwrap: %v %s", err, pr.Consults)
	}
	if consults[0]["id"] != "c-1" {
		t.Fatalf("consult payload: %v", consults[0])
	}
}

func TestPortalRecordSHR404IsEmptyBundle(t *testing.T) {
	svc, mux := portalRig(t,
		func(w http.ResponseWriter, r *http.Request) { http.NotFound(w, r) },
		func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"consults":[]}`)) })

	rec, pr := getRecordTok(t, mux, "patient_id=PT-1&phone=%2B2348030000000", elevate(t, svc, "PT-1"))
	if rec.Code != http.StatusOK || !pr.Complete {
		t.Fatalf("404 must not degrade completeness: %d %+v", rec.Code, pr)
	}
	if !strings.Contains(string(pr.Record), `"total":0`) {
		t.Fatalf("no-record-yet must be an honest empty bundle: %s", pr.Record)
	}
}

func TestPortalRecordLegDownDegradesHonestly(t *testing.T) {
	svc, mux := portalRig(t,
		func(w http.ResponseWriter, r *http.Request) {},
		func(w http.ResponseWriter, r *http.Request) {
			w.Write([]byte(`{"consults":[{"id":"c-9"}]}`))
		})
	_ = svc
	// Kill the SHR leg: point it at a port nothing listens on.
	t.Setenv("SHR_URL", "http://127.0.0.1:9")

	rec, pr := getRecordTok(t, mux, "patient_id=PT-1&phone=%2B2348030000000", elevate(t, svc, "PT-1"))
	if rec.Code != http.StatusOK {
		t.Fatalf("a dark SHR must never 500 the phone: %d", rec.Code)
	}
	if pr.Complete {
		t.Fatalf("dark SHR leg must flag incomplete: %+v", pr)
	}
	if len(pr.Record) != 0 && string(pr.Record) != "null" {
		t.Fatalf("failed leg must be explicit null, got %s", pr.Record)
	}
	if !strings.Contains(string(pr.Consults), `"c-9"`) {
		t.Fatalf("healthy leg must still serve: %s", pr.Consults)
	}
}

func TestPortalRecordConsultsNullBecomesEmptyArray(t *testing.T) {
	svc, mux := portalRig(t,
		func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(emptyBundle)) },
		func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"consults":null}`)) })

	_, pr := getRecordTok(t, mux, "patient_id=PT-1&phone=%2B2348030000000", elevate(t, svc, "PT-1"))
	if string(pr.Consults) != "[]" {
		t.Fatalf("null consults must normalise to []: %s", pr.Consults)
	}
}

func TestPortalRecordAuthGate(t *testing.T) {
	svc, mux := portalRig(t,
		func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(emptyBundle)) },
		func(w http.ResponseWriter, r *http.Request) { w.Write([]byte(`{"consults":[]}`)) })

	// Wrong phone — same 404 as unknown patient (no enumeration).
	rec, _ := getRecord(t, mux, "patient_id=PT-1&phone=%2B2348099999999")
	if rec.Code != http.StatusNotFound {
		t.Fatalf("wrong phone: %d", rec.Code)
	}
	rec, _ = getRecord(t, mux, "patient_id=PT-NOBODY&phone=%2B2348030000000")
	if rec.Code != http.StatusNotFound {
		t.Fatalf("unknown patient: %d", rec.Code)
	}
	rec, _ = getRecord(t, mux, "patient_id=&phone=")
	if rec.Code != http.StatusNotFound {
		t.Fatalf("missing params: %d", rec.Code)
	}

	// PHI additionally requires elevation: valid pair, no token → 428.
	rec, _ = getRecord(t, mux, "patient_id=PT-1&phone=%2B2348030000000")
	if rec.Code != 428 {
		t.Fatalf("no token must 428, got %d", rec.Code)
	}
	// A garbage token → 428 too.
	rec, _ = getRecordTok(t, mux, "patient_id=PT-1&phone=%2B2348030000000", "bogus")
	if rec.Code != 428 {
		t.Fatalf("bad token must 428, got %d", rec.Code)
	}
	// Elevated → 200.
	rec, _ = getRecordTok(t, mux, "patient_id=PT-1&phone=%2B2348030000000", elevate(t, svc, "PT-1"))
	if rec.Code != http.StatusOK {
		t.Fatalf("elevated read must 200, got %d", rec.Code)
	}
}
