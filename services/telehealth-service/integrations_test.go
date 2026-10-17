package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
)

type recordedWrite struct {
	resourceType, id, patientID, facilityID string
	body                                    map[string]any
}

type fakeSHR struct{ writes []recordedWrite }

func (f *fakeSHR) Put(patientID, facilityID, resourceType, id string, fhirBody any) error {
	raw, _ := json.Marshal(fhirBody)
	var body map[string]any
	json.Unmarshal(raw, &body)
	f.writes = append(f.writes, recordedWrite{resourceType, id, patientID, facilityID, body})
	return nil
}

type fakePharmacy struct{ routed []PharmacyDispenseRequest }

func (f *fakePharmacy) Route(req PharmacyDispenseRequest) error {
	f.routed = append(f.routed, req)
	return nil
}

func TestCloseWritesFHIREncounter(t *testing.T) {
	shr := &fakeSHR{}
	s := server{svc: NewService(), ix: Integrations{shr: shr}}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()

	resp, _ := http.Post(srv.URL+"/consults", "application/json", bodyJSON(t, map[string]string{
		"patient_id": "PT-9", "facility_id": "PHC-1", "nurse_id": "N-1", "complaint": "fever",
	}))
	var c Consult
	json.NewDecoder(resp.Body).Decode(&c)
	resp.Body.Close()

	r2, _ := http.Post(srv.URL+"/consults/"+c.ID+"/assess", "application/json", bodyJSON(t, map[string]string{
		"protocol": "IMCI", "assessment": "malaria suspected",
	}))
	r2.Body.Close()
	r3, _ := http.Post(srv.URL+"/consults/"+c.ID+"/close", "application/json", nil)
	r3.Body.Close()

	if len(shr.writes) != 1 {
		t.Fatalf("expected 1 SHR write, got %d", len(shr.writes))
	}
	w := shr.writes[0]
	if w.resourceType != "Encounter" || w.id != c.ID || w.patientID != "PT-9" || w.facilityID != "PHC-1" {
		t.Fatalf("bad encounter write: %+v", w)
	}
	if w.body["status"] != "finished" {
		t.Fatalf("encounter status = %v", w.body["status"])
	}
	subj := w.body["subject"].(map[string]any)["reference"]
	if subj != "Patient/PT-9" {
		t.Fatalf("subject = %v", subj)
	}
}

func TestPrescribeWritesMedicationRequestAndRoutesPharmacy(t *testing.T) {
	shr := &fakeSHR{}
	ph := &fakePharmacy{}
	s := server{svc: NewService(), ix: Integrations{shr: shr, pharmacy: ph}}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()

	resp, _ := http.Post(srv.URL+"/consults", "application/json", bodyJSON(t, map[string]string{
		"patient_id": "PT-9", "facility_id": "PHC-1", "nurse_id": "N-1", "complaint": "fever",
	}))
	var c Consult
	json.NewDecoder(resp.Body).Decode(&c)
	resp.Body.Close()
	r2, _ := http.Post(srv.URL+"/consults/"+c.ID+"/assess", "application/json", bodyJSON(t, map[string]string{
		"protocol": "IMCI", "assessment": "malaria confirmed RDT+",
	}))
	r2.Body.Close()

	r3, _ := http.Post(srv.URL+"/prescriptions", "application/json", bodyJSON(t, map[string]any{
		"consult_id": c.ID, "prescriber": "N-1", "medication": "AL8020",
		"dose": "4 tabs bd x 3 days", "quantity": 24, "pharmacy_id": "PHC-1",
		"patient_id": "PT-9", "facility_id": "PHC-1", "allergies": []string{},
	}))
	var out struct {
		Prescription   Prescription `json:"prescription"`
		PharmacyRouted bool         `json:"pharmacy_routed"`
	}
	json.NewDecoder(r3.Body).Decode(&out)
	r3.Body.Close()

	if !out.PharmacyRouted {
		t.Fatal("pharmacy should have accepted the route")
	}
	if len(ph.routed) != 1 || ph.routed[0].DrugCode != "AL8020" || ph.routed[0].Quantity != 24 {
		t.Fatalf("bad pharmacy route: %+v", ph.routed)
	}
	var mr *recordedWrite
	for i := range shr.writes {
		if shr.writes[i].resourceType == "MedicationRequest" {
			mr = &shr.writes[i]
		}
	}
	if mr == nil {
		t.Fatal("no MedicationRequest written to SHR")
	}
	if mr.body["intent"] != "order" || mr.body["status"] != "active" {
		t.Fatalf("bad MedicationRequest: %v", mr.body)
	}
}

func TestPrescribeWithoutIntegrationsStillWorks(t *testing.T) {
	s := server{svc: NewService()} // edge: no SHR, no pharmacy
	srv := httptest.NewServer(s.routes())
	defer srv.Close()
	resp, _ := http.Post(srv.URL+"/consults", "application/json", bodyJSON(t, map[string]string{
		"patient_id": "PT-1", "facility_id": "PHC-1", "nurse_id": "N-1", "complaint": "cough",
	}))
	var c Consult
	json.NewDecoder(resp.Body).Decode(&c)
	resp.Body.Close()
	r2, _ := http.Post(srv.URL+"/consults/"+c.ID+"/assess", "application/json", bodyJSON(t, map[string]string{
		"protocol": "IMCI", "assessment": "ARI",
	}))
	r2.Body.Close()
	r3, _ := http.Post(srv.URL+"/prescriptions", "application/json", bodyJSON(t, map[string]any{
		"consult_id": c.ID, "prescriber": "N-1", "medication": "AMOX500",
		"dose": "500mg tid", "quantity": 21, "pharmacy_id": "PHC-1",
	}))
	if r3.StatusCode != 200 {
		b, _ := io.ReadAll(r3.Body)
		t.Fatalf("edge prescribe failed: %s %s", r3.Status, b)
	}
	var out struct {
		PharmacyRouted bool `json:"pharmacy_routed"`
	}
	json.NewDecoder(r3.Body).Decode(&out)
	r3.Body.Close()
	if out.PharmacyRouted {
		t.Fatal("no pharmacy configured → routed must be false (pull fallback)")
	}
}

func TestListPrescriptionsPullFallback(t *testing.T) {
	svc := NewService()
	s := server{svc: svc}
	srv := httptest.NewServer(s.routes())
	defer srv.Close()
	resp, _ := http.Post(srv.URL+"/consults", "application/json", bodyJSON(t, map[string]string{
		"patient_id": "PT-1", "facility_id": "PHC-1", "nurse_id": "N-1", "complaint": "x",
	}))
	var c Consult
	json.NewDecoder(resp.Body).Decode(&c)
	resp.Body.Close()
	r2, _ := http.Post(srv.URL+"/consults/"+c.ID+"/assess", "application/json", bodyJSON(t, map[string]string{
		"protocol": "P", "assessment": "A",
	}))
	r2.Body.Close()
	r3, _ := http.Post(srv.URL+"/prescriptions", "application/json", bodyJSON(t, map[string]any{
		"consult_id": c.ID, "prescriber": "N-1", "medication": "ORS", "dose": "1 sachet", "quantity": 2, "pharmacy_id": "PHC-1",
	}))
	r3.Body.Close()

	r4, _ := http.Get(srv.URL + "/prescriptions?status=routed")
	var list []Prescription
	json.NewDecoder(r4.Body).Decode(&list)
	r4.Body.Close()
	if len(list) != 1 || list[0].Medication != "ORS" {
		t.Fatalf("pull fallback broken: %+v", list)
	}
}

func bodyJSON(t *testing.T, v any) io.Reader {
	t.Helper()
	raw, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return bytes.NewReader(raw)
}
