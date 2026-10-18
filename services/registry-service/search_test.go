package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func seedFacilities(t *testing.T) *MemoryStore {
	t.Helper()
	m := NewMemoryStore()
	for _, f := range []Facility{
		{ID: "PHC-BO-001", Name: "Maiduguri Baga Road PHC", State: "Borno", LGA: "Maiduguri"},
		{ID: "GH-BO-101", Name: "University of Maiduguri Teaching Hospital", State: "Borno", LGA: "Maiduguri"},
		{ID: "PHC-KN-001", Name: "Gwale PHC", State: "Kano", LGA: "Gwale"},
	} {
		if err := m.PutFacility(f); err != nil {
			t.Fatal(err)
		}
	}
	return m
}

func TestSearchTermsNormalisation(t *testing.T) {
	terms := searchTerms("  Maiduguri, (Baga)  ")
	if len(terms) != 2 || terms[0] != "maiduguri" || terms[1] != "baga" {
		t.Fatalf("terms: %v", terms)
	}
	if searchTerms(".,;") != nil {
		t.Fatal("punctuation-only query must yield no terms")
	}
}

func TestTsQueryPrefixAnd(t *testing.T) {
	got := tsQuery("mai dan")
	if got != "'mai':* & 'dan':*" {
		t.Fatalf("tsquery: %q", got)
	}
	// Quote/punctuation injection is stripped by normalisation, never
	// passed through to the tsquery string.
	if got := tsQuery("'; DROP"); got != "'drop':*" {
		t.Fatalf("injection normalisation: %q", got)
	}
}

func TestFacilitySearchPrefixMatch(t *testing.T) {
	m := seedFacilities(t)
	hits := m.SearchFacilities("maiduguri", 20)
	if len(hits) != 2 {
		t.Fatalf("want 2 Maiduguri hits, got %d", len(hits))
	}
	// Teaching hospital matches on name AND lga → outranks the PHC on lga-only... actually
	// both match name? PHC name contains Maiduguri too. Ranking by matched-field count:
	// GH-BO-101 matches name+lga (2), PHC-BO-001 matches name+lga (2) → tie broken by id.
	if hits[0].ID != "GH-BO-101" {
		t.Fatalf("stable id order on tie: %+v", hits)
	}
	// AND semantics: "maiduguri teaching" must exclude the PHC.
	hits = m.SearchFacilities("maiduguri teaching", 20)
	if len(hits) != 1 || hits[0].ID != "GH-BO-101" {
		t.Fatalf("AND semantics: %+v", hits)
	}
	// No match → empty, never an error.
	if hits := m.SearchFacilities("zanzibar", 20); len(hits) != 0 {
		t.Fatalf("no-match must be empty: %+v", hits)
	}
	// Limit respected.
	if hits := m.SearchFacilities("maiduguri", 1); len(hits) != 1 {
		t.Fatalf("limit: %d", len(hits))
	}
}

func TestFacilitySearchStateAndLGA(t *testing.T) {
	m := seedFacilities(t)
	hits := m.SearchFacilities("kano", 20)
	if len(hits) != 1 || hits[0].ID != "PHC-KN-001" {
		t.Fatalf("state search: %+v", hits)
	}
	hits = m.SearchFacilities("gwa", 20) // prefix: LGA Gwale + name Gwale
	if len(hits) != 1 || hits[0].ID != "PHC-KN-001" {
		t.Fatalf("prefix search: %+v", hits)
	}
}

func TestPatientSearchByNamePhoneNIN(t *testing.T) {
	m := NewMemoryPatientStore()
	p1, err := m.Register(Patient{Name: "Amina Bello", DOB: "1990-01-01", Phone: "+2348011111111", FacilityID: "F1"})
	if err != nil {
		t.Fatal(err)
	}
	p2, err := m.Register(Patient{Name: "Chidi Okafor", DOB: "1985-05-05", Phone: "+2348022222222", FacilityID: "F1"})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := m.LinkNIN(p2.ID, "12345678901"); err != nil {
		t.Fatal(err)
	}
	if hits := m.SearchPatients("amina", 20); len(hits) != 1 || hits[0].ID != p1.ID {
		t.Fatalf("name search: %+v", hits)
	}
	if hits := m.SearchPatients("234801", 20); len(hits) != 1 || hits[0].ID != p1.ID {
		t.Fatalf("phone prefix search (token split on +, like PG simple config): %+v", hits)
	}
	if hits := m.SearchPatients("12345", 20); len(hits) != 1 || hits[0].ID != p2.ID {
		t.Fatalf("nin prefix search: %+v", hits)
	}
}

func TestSearchHTTPEndpoints(t *testing.T) {
	s := server{store: seedFacilities(t)}
	mux := s.routes()
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/facilities/search?q=maiduguri", nil))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "PHC-BO-001") {
		t.Fatalf("facilities search: %d %s", rec.Code, rec.Body.String())
	}
	// One-char query is a full scan in disguise — rejected.
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/facilities/search?q=m", nil))
	if rec.Code != 400 {
		t.Fatalf("short q must be 400, got %d", rec.Code)
	}
	// /facilities/{id} subtree unaffected.
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/facilities/PHC-KN-001", nil))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "Gwale") {
		t.Fatalf("subtree get: %d %s", rec.Code, rec.Body.String())
	}
}

func TestPatientSearchHTTPEndpoint(t *testing.T) {
	ps := NewMemoryPatientStore()
	p, err := ps.Register(Patient{Name: "Funke Adeyemi", DOB: "1992-02-02", Phone: "+2348033333333", FacilityID: "F1"})
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	registerPatientRoutes(mux, ps)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/patients/search?q=funke", nil))
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), p.ID) {
		t.Fatalf("patients search: %d %s", rec.Code, rec.Body.String())
	}
	// Subtree fetch unaffected by the exact route.
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/patients/"+p.ID, nil))
	if rec.Code != 200 {
		t.Fatalf("subtree get: %d", rec.Code)
	}
}
