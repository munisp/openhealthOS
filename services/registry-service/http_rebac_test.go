package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/munisp/openhealthOS/shared/rebac"
)

func rebacFixture(t *testing.T) (*rebacAPI, http.Handler) {
	t.Helper()
	patients := NewMemoryPatientStore()
	if _, err := patients.Register(Patient{ID: "P1", Name: "Ada Eze", DOB: "1990-01-01", Phone: "+234801", FacilityID: "F1"}); err != nil {
		t.Fatal(err)
	}
	store := rebac.NewMemoryStore()
	a := newRebacAPI(&rebac.Checker{Store: store}, patients, []string{"op-1"})
	mux := http.NewServeMux()
	registerPatientRoutes(mux, patients)
	a.registerRoutes(mux)
	return a, a.gate(mux)
}

func grant(t *testing.T, h http.Handler, caller string, body grantBody) *httptest.ResponseRecorder {
	t.Helper()
	raw, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, "/rebac/grants", strings.NewReader(string(raw)))
	req.Header.Set("X-User-ID", caller)
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	return rr
}

func readPatient(h http.Handler, caller, id string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodGet, "/patients/"+id, nil)
	if caller != "" {
		req.Header.Set("X-User-ID", caller)
	}
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	return rr
}

// The full platform rule over HTTP: bootstrap operator seeds a facility
// admin; the admin grants staff; staff read their facility's patients;
// strangers and unattributed callers get 403.
func TestRebacEndToEnd(t *testing.T) {
	_, h := rebacFixture(t)

	// Unattributed: denied even though the patient exists.
	if rr := readPatient(h, "", "P1"); rr.Code != http.StatusForbidden {
		t.Fatalf("no X-User-ID: want 403, got %d", rr.Code)
	}
	// Stranger: denied.
	if rr := readPatient(h, "stranger", "P1"); rr.Code != http.StatusForbidden {
		t.Fatalf("stranger: want 403, got %d", rr.Code)
	}
	// Non-admin cannot grant.
	rr := grant(t, h, "stranger", grantBody{SubjectType: "user", SubjectID: "stranger", Relation: "staff", ObjectType: "facility", ObjectID: "F1"})
	if rr.Code != http.StatusForbidden {
		t.Fatalf("non-admin grant: want 403, got %d", rr.Code)
	}
	// Bootstrap operator seeds the facility admin.
	rr = grant(t, h, "op-1", grantBody{SubjectType: "user", SubjectID: "admin-ada", Relation: "admin", ObjectType: "facility", ObjectID: "F1"})
	if rr.Code != 200 {
		t.Fatalf("bootstrap grant: %d %s", rr.Code, rr.Body.String())
	}
	// The admin grants staff to a nurse.
	rr = grant(t, h, "admin-ada", grantBody{SubjectType: "user", SubjectID: "nurse-ngozi", Relation: "staff", ObjectType: "facility", ObjectID: "F1"})
	if rr.Code != 200 {
		t.Fatalf("admin staff grant: %d %s", rr.Code, rr.Body.String())
	}
	// The nurse reads her facility's patient.
	if rr := readPatient(h, "nurse-ngozi", "P1"); rr.Code != 200 {
		t.Fatalf("staff read: want 200, got %d %s", rr.Code, rr.Body.String())
	}
	// FHIR surface is gated identically.
	req := httptest.NewRequest(http.MethodGet, "/fhir/Patient/P1", nil)
	req.Header.Set("X-User-ID", "nurse-ngozi")
	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("fhir read: want 200, got %d", rr.Code)
	}
	// Guardian direct grant (patient object) managed by the facility admin.
	rr = grant(t, h, "admin-ada", grantBody{SubjectType: "user", SubjectID: "mama-ndidi", Relation: "guardian", ObjectType: "patient", ObjectID: "P1"})
	if rr.Code != 200 {
		t.Fatalf("guardian grant: %d", rr.Code)
	}
	if rr := readPatient(h, "mama-ndidi", "P1"); rr.Code != 200 {
		t.Fatalf("guardian read: want 200, got %d", rr.Code)
	}
	// Revoke staff: the nurse loses access immediately.
	raw, _ := json.Marshal(grantBody{SubjectType: "user", SubjectID: "nurse-ngozi", Relation: "staff", ObjectType: "facility", ObjectID: "F1"})
	req = httptest.NewRequest(http.MethodDelete, "/rebac/grants", strings.NewReader(string(raw)))
	req.Header.Set("X-User-ID", "admin-ada")
	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("revoke: %d", rr.Code)
	}
	if rr := readPatient(h, "nurse-ngozi", "P1"); rr.Code != http.StatusForbidden {
		t.Fatalf("after revoke: want 403, got %d", rr.Code)
	}
}

// The audit view lists live grants for admins only.
func TestGrantsListAudit(t *testing.T) {
	_, h := rebacFixture(t)
	grant(t, h, "op-1", grantBody{SubjectType: "user", SubjectID: "admin-ada", Relation: "admin", ObjectType: "facility", ObjectID: "F1"})
	grant(t, h, "admin-ada", grantBody{SubjectType: "user", SubjectID: "nurse-ngozi", Relation: "staff", ObjectType: "facility", ObjectID: "F1"})

	req := httptest.NewRequest(http.MethodGet, "/rebac/grants?object_type=facility&object_id=F1", nil)
	req.Header.Set("X-User-ID", "admin-ada")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("list: %d", rr.Code)
	}
	var out struct {
		Tuples []rebac.Tuple `json:"tuples"`
	}
	json.NewDecoder(rr.Body).Decode(&out)
	if len(out.Tuples) != 2 {
		t.Fatalf("tuples: %+v", out.Tuples)
	}

	req = httptest.NewRequest(http.MethodGet, "/rebac/grants?object_type=facility&object_id=F1", nil)
	req.Header.Set("X-User-ID", "nurse-ngozi")
	rr = httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("non-admin list: want 403, got %d", rr.Code)
	}
}

// Ungated paths (search, list, unknown patients) behave as before.
func TestGateLeavesOtherPathsAlone(t *testing.T) {
	_, h := rebacFixture(t)
	req := httptest.NewRequest(http.MethodGet, "/patients/search?q=ada", nil)
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("search gated: %d", rr.Code)
	}
	if rr := readPatient(h, "anyone", "NOPE"); rr.Code != http.StatusNotFound {
		t.Fatalf("unknown patient: want 404, got %d", rr.Code)
	}
}
