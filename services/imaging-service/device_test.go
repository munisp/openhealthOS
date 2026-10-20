package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestMWLScheduleQuery(t *testing.T) {
	m := NewMWL(func() time.Time { return time.Date(2026, 10, 20, 8, 0, 0, 0, time.UTC) })
	it, err := m.Schedule(WorklistItem{
		PatientID: "P1", Modality: "mr", Procedure: "MRI brain w/o contrast",
		StationAE: "MRI1", Scheduled: "2026-10-20T10:00:00Z",
	})
	if err != nil {
		t.Fatal(err)
	}
	if it.ID == "" || it.Status != "scheduled" || it.Modality != "MR" {
		t.Fatalf("bad schedule: %+v", it)
	}
	if _, err := m.Schedule(WorklistItem{PatientID: "P2"}); err == nil {
		t.Fatal("missing modality/procedure must fail")
	}
	got := m.Query("MR", "MRI1", "2026-10-20", true)
	if len(got) != 1 || got[0].ID != it.ID {
		t.Fatalf("query: %+v", got)
	}
	if len(m.Query("CT", "", "", false)) != 0 {
		t.Fatal("modality filter failed")
	}
	if len(m.Query("MR", "OTHER_STATION", "", false)) != 0 {
		t.Fatal("station filter failed")
	}
}

func TestMWLTransitionGuard(t *testing.T) {
	m := NewMWL(time.Now)
	it, _ := m.Schedule(WorklistItem{PatientID: "P1", Modality: "CT", Procedure: "CT chest"})
	if _, err := m.Advance(it.ID, "completed", "tech1"); err == nil {
		t.Fatal("scheduled → completed must be rejected")
	}
	if _, err := m.Advance(it.ID, "arrived", "tech1"); err != nil {
		t.Fatal(err)
	}
	if _, err := m.Advance(it.ID, "in-progress", "tech1"); err != nil {
		t.Fatal(err)
	}
	done, err := m.Advance(it.ID, "completed", "tech1")
	if err != nil || done.Status != "completed" {
		t.Fatal(err)
	}
	if _, err := m.Advance(it.ID, "scheduled", "tech1"); err == nil {
		t.Fatal("completed → scheduled must be rejected")
	}
	if _, err := m.Advance("MWL-999", "arrived", "tech1"); err != errMWLNotFound {
		t.Fatal("unknown id must 404")
	}
	if len(m.Query("", "", "", true)) != 0 {
		t.Fatal("completed item must leave the active worklist")
	}
}

func TestDeviceReadingValidate(t *testing.T) {
	bad := DeviceReading{DeviceID: "D1"}
	if bad.Validate() == nil {
		t.Fatal("missing fields must fail")
	}
	ok := DeviceReading{DeviceID: "D1", PatientID: "P1", Code: "8867-4", Value: 72, Unit: "/min"}
	if err := ok.Validate(); err != nil {
		t.Fatal(err)
	}
	ok.Effective = "not-a-date"
	if ok.Validate() == nil {
		t.Fatal("bad effective must fail")
	}
}

func TestDeviceIngestFailSoft(t *testing.T) {
	// SHR unreachable: reading still stored + returned, forwarded=false.
	d := NewDeviceIngest("http://127.0.0.1:1", time.Now)
	ob, fwd, err := d.Ingest(DeviceReading{
		DeviceID: "OHAST1.fac1.EQ-000001.1.sig", PatientID: "P1",
		Code: "8867-4", CodeText: "Heart rate", Value: 72, Unit: "/min",
	})
	if err != nil {
		t.Fatal(err)
	}
	if fwd {
		t.Fatal("unreachable SHR must report forwarded=false")
	}
	if ob["resourceType"] != "Observation" || ob["status"] != "final" {
		t.Fatalf("bad observation: %+v", ob)
	}
	st := d.Stats()
	if st["failed"].(int) != 1 || st["recent"].(int) != 1 {
		t.Fatalf("stats: %+v", st)
	}
}

func TestDeviceIngestForwardOK(t *testing.T) {
	var got map[string]any
	shr := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/fhir/Observation" {
			t.Errorf("bad path %s", r.URL.Path)
		}
		json.NewDecoder(r.Body).Decode(&got)
		w.WriteHeader(201)
	}))
	defer shr.Close()
	d := NewDeviceIngest(shr.URL, func() time.Time { return time.Date(2026, 10, 20, 9, 0, 0, 0, time.UTC) })
	_, fwd, err := d.Ingest(DeviceReading{
		DeviceID: "EQ-000002", PatientID: "P9", Code: "8480-6",
		Value: 120, Unit: "mm[Hg]", Encounter: "ENC-1",
	})
	if err != nil || !fwd {
		t.Fatalf("fwd=%v err=%v", fwd, err)
	}
	if got["subject"].(map[string]any)["reference"] != "Patient/P9" {
		t.Fatalf("payload: %+v", got)
	}
	if got["encounter"].(map[string]any)["reference"] != "Encounter/ENC-1" {
		t.Fatal("encounter lost")
	}
	if d.Stats()["forwarded"].(int) != 1 {
		t.Fatal("forward stat not bumped")
	}
}

func TestDeviceHTTPEndToEnd(t *testing.T) {
	pacs := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer pacs.Close()
	srv := newTestServer(t, pacs)
	mux := http.NewServeMux()
	srv.routes(mux)
	ts := httptest.NewServer(mux)
	defer ts.Close()

	// schedule + pull MWL
	resp, err := http.Post(ts.URL+"/mwl", "application/json",
		jsonBody(map[string]any{"patient_id": "P1", "modality": "MR", "procedure": "MRI knee", "station_ae": "MRI1"}))
	if err != nil || resp.StatusCode != 201 {
		t.Fatalf("schedule: %v %v", resp.StatusCode, err)
	}
	resp.Body.Close()
	resp, err = http.Get(ts.URL + "/mwl?modality=MR&station=MRI1&active=true")
	if err != nil {
		t.Fatal(err)
	}
	var list struct {
		Items []WorklistItem `json:"items"`
	}
	json.NewDecoder(resp.Body).Decode(&list)
	resp.Body.Close()
	if len(list.Items) != 1 {
		t.Fatalf("mwl pull: %+v", list)
	}

	// ingest a reading
	resp, err = http.Post(ts.URL+"/devices/readings", "application/json",
		jsonBody(map[string]any{"device_id": "D1", "patient_id": "P1", "code": "8867-4", "value": 72, "unit": "/min"}))
	if err != nil || resp.StatusCode != 202 {
		t.Fatalf("ingest: %v %v", resp.StatusCode, err)
	}
	resp.Body.Close()
	resp, err = http.Get(ts.URL + "/devices/stats")
	if err != nil {
		t.Fatal(err)
	}
	var st map[string]any
	json.NewDecoder(resp.Body).Decode(&st)
	resp.Body.Close()
	if st["recent"].(float64) != 1 {
		t.Fatalf("stats: %+v", st)
	}
}

func jsonBody(v any) *bytes.Reader {
	b, _ := json.Marshal(v)
	return bytes.NewReader(b)
}
