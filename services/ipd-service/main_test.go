package main

// IPD domain tests (ADR-0017): the bed-board invariants and the full
// ADT day — admit, transfer (atomicity under failure), discharge,
// housekeeping — plus the HTTP surface and the discharge write-back.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func boardWithBeds(t *testing.T, store Store) {
	t.Helper()
	if err := store.PutWard(Ward{ID: "W-MED", FacilityID: "F-1", Name: "Medical", Kind: "medical"}); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"B-1", "B-2", "B-3"} {
		if err := store.PutBed(Bed{ID: id, WardID: "W-MED", Label: "M" + id[len(id)-1:]}); err != nil {
			t.Fatal(err)
		}
	}
}

func TestAdmitInvariants(t *testing.T) {
	s := NewMemoryStore()
	boardWithBeds(t, s)
	a := Admission{ID: "ADM-0001", PatientID: "PT-1", FacilityID: "F-1", WardID: "W-MED",
		BedID: "B-1", Reason: "pneumonia", Status: AdmOpen, AdmittedAt: "2026-10-18T08:00:00Z"}
	if err := s.Admit(a); err != nil {
		t.Fatalf("admit: %v", err)
	}
	// One patient, one open admission.
	if err := s.Admit(Admission{ID: "ADM-0002", PatientID: "PT-1", BedID: "B-2", WardID: "W-MED",
		Status: AdmOpen}); err != ErrAlreadyAdmitted {
		t.Fatalf("double admission must fail, got %v", err)
	}
	// One bed, one patient.
	if err := s.Admit(Admission{ID: "ADM-0003", PatientID: "PT-2", BedID: "B-1", WardID: "W-MED",
		Status: AdmOpen}); err != ErrBedOccupied {
		t.Fatalf("occupied bed must refuse, got %v", err)
	}
	// Unknown bed.
	if err := s.Admit(Admission{ID: "ADM-0004", PatientID: "PT-3", BedID: "B-X", WardID: "W-MED",
		Status: AdmOpen}); err != ErrNotFound {
		t.Fatalf("unknown bed, got %v", err)
	}
	bed, _ := s.Bed("B-1")
	if bed.State != BedOccupied {
		t.Fatalf("bed state: %s", bed.State)
	}
}

func TestTransferIsAtomic(t *testing.T) {
	s := NewMemoryStore()
	boardWithBeds(t, s)
	s.Admit(Admission{ID: "ADM-0001", PatientID: "PT-1", BedID: "B-1", WardID: "W-MED", Status: AdmOpen})
	s.Admit(Admission{ID: "ADM-0002", PatientID: "PT-2", BedID: "B-3", WardID: "W-MED", Status: AdmOpen})

	// Transfer onto an occupied bed fails and leaves the patient exactly
	// where they were — no stranding.
	if _, err := s.Transfer("ADM-0001", "B-3", "2026-10-18T10:00:00Z"); err != ErrBedOccupied {
		t.Fatalf("occupied destination must fail, got %v", err)
	}
	a, _ := s.Admission("ADM-0001")
	if a.BedID != "B-1" || len(a.Moves) != 0 {
		t.Fatalf("failed transfer must not move the patient: %+v", a)
	}
	b1, _ := s.Bed("B-1")
	if b1.State != BedOccupied {
		t.Fatalf("source bed untouched: %s", b1.State)
	}

	// Clean transfer: destination taken, source into cleaning, move logged.
	a, err := s.Transfer("ADM-0001", "B-2", "2026-10-18T11:00:00Z")
	if err != nil {
		t.Fatalf("transfer: %v", err)
	}
	if a.BedID != "B-2" || len(a.Moves) != 1 || a.Moves[0].FromBedID != "B-1" {
		t.Fatalf("transfer result: %+v", a)
	}
	b2, _ := s.Bed("B-2")
	b1, _ = s.Bed("B-1")
	if b2.State != BedOccupied || b1.State != BedCleaning {
		t.Fatalf("bed states after transfer: %s / %s", b2.State, b1.State)
	}
	// Housekeeping returns the cleaned bed.
	if err := s.SetBedState("B-1", BedFree); err != nil {
		t.Fatal(err)
	}
}

func TestDischargeFreesBedIntoCleaning(t *testing.T) {
	s := NewMemoryStore()
	boardWithBeds(t, s)
	s.Admit(Admission{ID: "ADM-0001", PatientID: "PT-1", BedID: "B-1", WardID: "W-MED", Status: AdmOpen})
	a, err := s.Discharge("ADM-0001", "2026-10-20T09:00:00Z", "responded to antibiotics; review in 2 weeks")
	if err != nil {
		t.Fatalf("discharge: %v", err)
	}
	if a.Status != AdmDischarged || a.Summary == "" {
		t.Fatalf("discharge result: %+v", a)
	}
	b, _ := s.Bed("B-1")
	if b.State != BedCleaning {
		t.Fatalf("discharged bed must go to cleaning, got %s", b.State)
	}
	// A discharged patient can be admitted again later (once a bed is free).
	if _, err := s.OpenAdmission("PT-1"); err != ErrNotFound {
		t.Fatalf("no open admission after discharge, got %v", err)
	}
	// Double discharge is refused.
	if _, err := s.Discharge("ADM-0001", "2026-10-21T09:00:00Z", ""); err != ErrNotAdmitted {
		t.Fatalf("double discharge, got %v", err)
	}
}

func TestHTTPADTLifecycle(t *testing.T) {
	s := NewMemoryStore()
	boardWithBeds(t, s)
	srv := server{store: s, ix: Integrations{}, now: func() time.Time {
		return time.Date(2026, 10, 18, 8, 0, 0, 0, time.UTC)
	}}
	mux := srv.routes()

	post := func(path, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, path, strings.NewReader(body))
		rr := httptest.NewRecorder()
		mux.ServeHTTP(rr, req)
		return rr
	}
	rr := post("/admissions", `{"patient_id":"PT-9","facility_id":"F-1","ward_id":"W-MED","bed_id":"B-1","reason":"malaria"}`)
	if rr.Code != 201 {
		t.Fatalf("admit: %d %s", rr.Code, rr.Body)
	}
	var adm Admission
	json.NewDecoder(rr.Body).Decode(&adm)
	if adm.ID == "" || adm.Status != AdmOpen {
		t.Fatalf("admission body: %+v", adm)
	}
	// Occupied bed → 409.
	rr = post("/admissions", `{"patient_id":"PT-10","facility_id":"F-1","ward_id":"W-MED","bed_id":"B-1"}`)
	if rr.Code != http.StatusConflict {
		t.Fatalf("occupied bed must be 409, got %d", rr.Code)
	}
	// Transfer then discharge.
	rr = post("/admissions/"+adm.ID+"/transfer", `{"to_bed_id":"B-2"}`)
	if rr.Code != 200 {
		t.Fatalf("transfer: %d %s", rr.Code, rr.Body)
	}
	rr = post("/admissions/"+adm.ID+"/discharge", `{"summary":"recovered"}`)
	if rr.Code != 200 {
		t.Fatalf("discharge: %d %s", rr.Code, rr.Body)
	}
	// Patient-scoped open admission query answers null after discharge.
	req := httptest.NewRequest(http.MethodGet, "/admissions?patient_id=PT-9", nil)
	rr = httptest.NewRecorder()
	mux.ServeHTTP(rr, req)
	if !strings.Contains(rr.Body.String(), `"admission":null`) {
		t.Fatalf("open admission query: %s", rr.Body)
	}
}

func TestDischargeWritesBackToSHR(t *testing.T) {
	var puts []string
	shr := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPut && strings.HasPrefix(r.URL.Path, "/fhir/") {
			var res map[string]any
			json.NewDecoder(r.Body).Decode(&res)
			rt, _ := res["resourceType"].(string)
			puts = append(puts, rt)
			if rt == "Encounter" {
				cls, _ := res["class"].(map[string]any)
				if cls["code"] != "IMP" {
					t.Errorf("encounter class must be IMP: %v", cls)
				}
				subj, _ := res["subject"].(map[string]any)
				if subj["reference"] != "Patient/PT-1" {
					t.Errorf("subject: %v", subj)
				}
			}
			w.WriteHeader(200)
			return
		}
		w.WriteHeader(404)
	}))
	defer shr.Close()

	ix := Integrations{shrURL: shr.URL, client: shr.Client()}
	ix.postDischargeSummary(Admission{
		ID: "ADM-0001", PatientID: "PT-1", FacilityID: "F-1", Reason: "pneumonia",
		AdmittedAt: "2026-10-18T08:00:00Z", DischargedAt: "2026-10-20T09:00:00Z",
		Summary: "responded well",
	})
	if len(puts) != 2 || puts[0] != "Encounter" || puts[1] != "DocumentReference" {
		t.Fatalf("write-back must post Encounter + DocumentReference, got %v", puts)
	}
}
