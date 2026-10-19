package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func newSvc(t *testing.T) *Service {
	t.Helper()
	store, err := NewStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return &Service{store: store, planner: stubPlanner{}}
}

type stubPlanner struct{ lastBundle map[string]any }

func (p stubPlanner) Plan(patient string, bundle map[string]any) (map[string]any, error) {
	return map[string]any{"patient_id": patient, "recommendations": []string{"test"}}, nil
}

type spyMirror struct {
	puts int
	fail bool
}

func (m *spyMirror) Put(_ string, _ map[string]any) error {
	if m.fail {
		return errors.New("shr down")
	}
	m.puts++
	return nil
}

func stepsReq(ref string, val float64) IngestRequest {
	return IngestRequest{PatientID: "p1", Source: "health-connect",
		Observations: []IngestObs{{Type: "steps", Value: val, Unit: "steps",
			Ts: time.Now().UTC().Format(time.RFC3339), Ref: ref}}}
}

func TestIngestStoresAndMirrors(t *testing.T) {
	svc := newSvc(t)
	m := &spyMirror{}
	svc.mirror = m
	res, err := svc.ingest(stepsReq("r1", 4200))
	if err != nil {
		t.Fatal(err)
	}
	if res["accepted"].(int) != 1 || m.puts != 1 {
		t.Fatalf("accepted=%v puts=%d", res["accepted"], m.puts)
	}
}

func TestIngestExactlyOnce(t *testing.T) {
	svc := newSvc(t)
	svc.ingest(stepsReq("r1", 4200))
	res, _ := svc.ingest(stepsReq("r1", 4200))
	if res["accepted"].(int) != 0 || res["deduped"].(int) != 1 {
		t.Fatalf("res=%v", res)
	}
	if n := len(svc.store.ForPatient("p1")); n != 1 {
		t.Fatalf("journal has %d rows, want 1", n)
	}
}

func TestUnknownSourceRefused(t *testing.T) {
	svc := newSvc(t)
	req := stepsReq("r1", 1)
	req.Source = "strava" // not on the allow-list
	_, err := svc.ingest(req)
	if !errors.Is(err, ErrUnknownSource) {
		t.Fatalf("want ErrUnknownSource, got %v", err)
	}
}

func TestUnknownTypeRefused(t *testing.T) {
	svc := newSvc(t)
	req := stepsReq("r1", 1)
	req.Observations[0].Type = "dna-sequence"
	if _, err := svc.ingest(req); !errors.Is(err, ErrUnknownType) {
		t.Fatalf("want ErrUnknownType, got %v", err)
	}
}

func TestMirrorFailureKeepsLocal(t *testing.T) {
	svc := newSvc(t)
	svc.mirror = &spyMirror{fail: true}
	res, err := svc.ingest(stepsReq("r1", 100))
	if err != nil || res["accepted"].(int) != 1 {
		t.Fatalf("mirror failure must not fail ingest: %v %v", res, err)
	}
	// local journal holds the row even though the mirror failed
	if n := len(svc.store.ForPatient("p1")); n != 1 {
		t.Fatalf("journal lost the row: %d", n)
	}
}

func TestFHIRMapping(t *testing.T) {
	o := StoredObs{ID: "wo-x", PatientID: "p1", Source: "fitbit",
		Type: "steps", Value: 5000, Unit: "steps", Ts: "2026-10-19T08:00:00Z",
		FHIRID: "wo-x"}
	f := o.toFHIR()
	if f["resourceType"] != "Observation" {
		t.Fatal("not an Observation")
	}
	code := f["code"].(map[string]any)["coding"].([]map[string]string)[0]["code"]
	if code != "55423-8" {
		t.Fatalf("code=%v", code)
	}
	if f["subject"].(map[string]string)["reference"] != "Patient/p1" {
		t.Fatal("subject missing")
	}
	if _, ok := f["valueQuantity"]; !ok {
		t.Fatal("numeric types carry valueQuantity")
	}
	w := StoredObs{Type: "workout", Text: "30min run"}
	if _, ok := w.toFHIR()["valueString"]; !ok {
		t.Fatal("workout carries valueString")
	}
}

func TestSummaryAggregates(t *testing.T) {
	svc := newSvc(t)
	now := time.Now().UTC()
	day := func(n int) string { return now.AddDate(0, 0, -n).Format(time.RFC3339) }
	svc.ingest(IngestRequest{PatientID: "p1", Source: "manual", Observations: []IngestObs{
		{Type: "steps", Value: 4000, Ts: day(0), Ref: "s0"},
		{Type: "steps", Value: 6000, Ts: day(3), Ref: "s3"},
		{Type: "workout", Text: "run", Ts: day(2), Ref: "w1"},
		{Type: "workout", Text: "gym", Ts: day(10), Ref: "w2"}, // >7d: excluded
		{Type: "nutrition", Text: "lunch: rice+beans", Ts: day(1), Ref: "n1"},
		{Type: "sleep", Value: 7.5, Unit: "h", Ts: day(1), Ref: "sl1"},
		{Type: "weight", Value: 81.5, Unit: "kg", Ts: day(1), Ref: "wt1"},
	}})
	sum := svc.summary("p1", now)
	if sum["steps_today"].(float64) != 4000 {
		t.Fatalf("steps_today=%v", sum["steps_today"])
	}
	if sum["avg_daily_steps_14d"].(float64) != 5000 {
		t.Fatalf("avg=%v", sum["avg_daily_steps_14d"])
	}
	if sum["workouts_7d"].(int) != 1 {
		t.Fatalf("workouts=%v", sum["workouts_7d"])
	}
	if sum["nutrition_days_7d"].(int) != 1 {
		t.Fatalf("nutrition=%v", sum["nutrition_days_7d"])
	}
	if sum["avg_sleep_hours_7d"].(float64) != 7.5 {
		t.Fatalf("sleep=%v", sum["avg_sleep_hours_7d"])
	}
	if sum["latest_weight"].(map[string]any)["value"].(float64) != 81.5 {
		t.Fatalf("weight=%v", sum["latest_weight"])
	}
	if srcs := sum["sources"].([]string); len(srcs) != 1 || srcs[0] != "manual" {
		t.Fatalf("sources=%v", srcs)
	}
}

func TestStoreSurvivesRestart(t *testing.T) {
	dir := t.TempDir()
	s1, _ := NewStore(dir)
	s1.Add(StoredObs{ID: "wo-1", PatientID: "p1", Source: "manual", Type: "steps",
		Value: 1, Ts: day0(), Ref: "r1"})
	s2, _ := NewStore(dir)
	if n := len(s2.ForPatient("p1")); n != 1 {
		t.Fatalf("reloaded %d rows", n)
	}
	// replay after restart dedupes
	o, fresh, _ := s2.Add(StoredObs{ID: "wo-1", PatientID: "p1", Source: "manual",
		Type: "steps", Value: 1, Ts: day0(), Ref: "r1"})
	if fresh || o.ID != "wo-1" {
		t.Fatal("replay after restart must dedupe")
	}
}

func day0() string { return time.Now().UTC().Format(time.RFC3339) }

func TestHTTPRoutes(t *testing.T) {
	svc := newSvc(t)
	ts := httptest.NewServer(svc.routes())
	defer ts.Close()

	resp, err := http.Post(ts.URL+"/wellness/ingest", "application/json",
		strings.NewReader(`{"patient_id":"p1","source":"fitbit","observations":[{"type":"steps","value":9000,"unit":"steps","ref":"r9"}]}`))
	if err != nil || resp.StatusCode != 200 {
		t.Fatalf("ingest: %v %v", resp.StatusCode, err)
	}
	var sum map[string]any
	get(t, ts.URL+"/wellness/p1/summary", &sum)
	if sum["steps_today"].(float64) != 9000 {
		t.Fatalf("summary=%v", sum)
	}
	var plan map[string]any
	get(t, ts.URL+"/wellness/p1/plan?age=45&sex=male", &plan)
	if plan["patient_id"] != "p1" {
		t.Fatalf("plan=%v", plan)
	}
	var sources map[string]any
	get(t, ts.URL+"/wellness/sources", &sources)
	if _, ok := sources["sources"].(map[string]any)["health-connect"]; !ok {
		t.Fatalf("sources=%v", sources)
	}

	resp2, _ := http.Post(ts.URL+"/wellness/ingest", "application/json",
		strings.NewReader(`{"patient_id":"p1","source":"strava","observations":[{"type":"steps","value":1}]}`))
	if resp2.StatusCode != http.StatusForbidden {
		t.Fatalf("unknown source must be 403, got %d", resp2.StatusCode)
	}
}

func get(t *testing.T, url string, out any) {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		t.Fatal(err)
	}
}
