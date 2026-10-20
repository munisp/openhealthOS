package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func newTestServer(t *testing.T, pacs *httptest.Server) server {
	t.Helper()
	return server{
		pacs:     NewPACSClient(pacs.URL),
		worklist: NewWorklist(),
		videos:   NewVideoQueue(nil),
		mwl:      NewMWL(time.Now),
		devices:  NewDeviceIngest("", time.Now),
		now:      time.Now,
	}
}

// fakePACS echoes DICOMweb calls so the facade can be tested end to end.
func fakePACS(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/dicom+json")
		w.Write([]byte(`[{"ok":true,"path":"` + r.URL.Path + `"}]`))
	}))
}

func req(t *testing.T, s server, method, path, body, actor string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	if actor != "" {
		r.Header.Set("X-Actor", actor)
	}
	rec := httptest.NewRecorder()
	mux := http.NewServeMux()
	s.routes(mux)
	mux.ServeHTTP(rec, r)
	return rec
}

func TestDICOMwebFacadeProxies(t *testing.T) {
	s := newTestServer(t, fakePACS(t))
	rec := req(t, s, "GET", "/dicomweb/studies?limit=10", "", "rad-1")
	if rec.Code != 200 || !strings.Contains(rec.Body.String(), "/dicom-web/studies") {
		t.Fatalf("qido proxy: %d %s", rec.Code, rec.Body)
	}
	rec = req(t, s, "GET", "/dicomweb/studies/1.2.3/series", "", "rad-1")
	if !strings.Contains(rec.Body.String(), "/dicom-web/studies/1.2.3/series") {
		t.Fatalf("series proxy: %s", rec.Body)
	}
}

func TestSTOWHeaderFallbackRegistersWorklist(t *testing.T) {
	s := newTestServer(t, fakePACS(t))
	r := httptest.NewRequest("POST", "/dicomweb/studies", strings.NewReader("dicom-bytes"))
	r.Header.Set("X-Modality", "CR")
	r.Header.Set("X-Study-UID", "1.2.840.1")
	r.Header.Set("X-Study-Description", "Chest trauma screen")
	r.Header.Set("X-Patient-ID", "PAT-44")
	rec := httptest.NewRecorder()
	mux := http.NewServeMux()
	s.routes(mux)
	mux.ServeHTTP(rec, r)
	if rec.Code != 200 {
		t.Fatalf("stow: %d", rec.Code)
	}
	list := s.worklist.List(false, time.Now())
	if len(list) != 1 || list[0].Priority != "stat" {
		t.Fatalf("trauma chest must triage STAT, got %+v", list)
	}
}

func TestWorklistOrderingAndSLA(t *testing.T) {
	s := newTestServer(t, fakePACS(t))
	s.worklist.Register(Study{StudyUID: "A", Modality: "MR", Description: "Knee"})
	s.worklist.Register(Study{StudyUID: "B", Modality: "CR", Description: "Chest"})
	s.worklist.Register(Study{StudyUID: "C", Modality: "CT", Description: "Head bleed query"})
	list := s.worklist.List(false, time.Now())
	if list[0].StudyUID != "C" || list[1].StudyUID != "B" || list[2].StudyUID != "A" {
		t.Fatalf("want stat→urgent→routine, got %v %v %v", list[0].StudyUID, list[1].StudyUID, list[2].StudyUID)
	}
	// duplicate registration is idempotent (webhooks fire twice)
	s.worklist.Register(Study{StudyUID: "A", Modality: "MR", Description: "Knee"})
	if len(s.worklist.List(false, time.Now())) != 3 {
		t.Fatal("duplicate registration")
	}
	// SLA: C (stat) overdue after 1h, A (routine) not
	past := time.Now().Add(-2 * time.Hour)
	s.worklist.mu.Lock()
	s.worklist.studies["C"].ReceivedAt = past
	s.worklist.mu.Unlock()
	over := s.worklist.List(true, time.Now())
	if len(over) != 1 || over[0].StudyUID != "C" {
		t.Fatalf("overdue: %+v", over)
	}
	// sign clears overdue
	if err := s.worklist.Sign("C", "dr-eze"); err != nil {
		t.Fatal(err)
	}
	if len(s.worklist.List(true, time.Now())) != 0 {
		t.Fatal("signed study still overdue")
	}
}

func TestPrioritizeNeedsActorAndReason(t *testing.T) {
	s := newTestServer(t, fakePACS(t))
	s.worklist.Register(Study{StudyUID: "A", Modality: "MR", Description: "Knee"})
	rec := req(t, s, "POST", "/worklist/A/priority", `{"level":"stat","reason":"","actor":""}`, "")
	if rec.Code != 422 {
		t.Fatalf("anonymous reprioritization must fail: %d", rec.Code)
	}
	rec = req(t, s, "POST", "/worklist/A/priority", `{"level":"stat","reason":"clinical call","actor":"dr-eze"}`, "")
	if rec.Code != 200 {
		t.Fatalf("prioritize: %d %s", rec.Code, rec.Body)
	}
	if s.worklist.List(false, time.Now())[0].Priority != "stat" {
		t.Fatal("priority not applied")
	}
}

func TestExportPlanKAnonymity(t *testing.T) {
	s := newTestServer(t, fakePACS(t))
	s.worklist.Register(Study{StudyUID: "U1", Modality: "US", Description: "Obstetric"})
	// cohort below k=5 refused
	rec := req(t, s, "POST", "/studies/U1/export-plan", `{"cohort_size":3,"pseudonym":"PSEUDO-1"}`, "")
	if rec.Code != 422 {
		t.Fatalf("k-anonymity floor: %d", rec.Code)
	}
	rec = req(t, s, "POST", "/studies/U1/export-plan", `{"cohort_size":12,"pseudonym":"PSEUDO-1"}`, "")
	if rec.Code != 200 {
		t.Fatalf("plan: %d %s", rec.Code, rec.Body)
	}
	var plan DeIDPlan
	json.Unmarshal(rec.Body.Bytes(), &plan)
	if !plan.BurnedInPixelRisk {
		t.Fatal("ultrasound must flag burned-in pixel risk")
	}
	for _, tag := range plan.KeepTags {
		if tag == "PatientName" || tag == "PatientID" || tag == "PatientBirthDate" {
			t.Fatalf("direct identifier leaked into keep-list: %s", tag)
		}
	}
	// pseudonym mandatory
	if rec := req(t, s, "POST", "/studies/U1/export-plan", `{"cohort_size":12,"pseudonym":""}`, ""); rec.Code != 422 {
		t.Fatal("real patient ids must never export")
	}
}

func TestVideoJobLifecycle(t *testing.T) {
	s := newTestServer(t, fakePACS(t))
	// source must be a media blob ref
	if rec := req(t, s, "POST", "/videos", `{"source_ref":"file:///tmp/x.mp4"}`, "tech-1"); rec.Code != 422 {
		t.Fatalf("non-blob source refused: %d", rec.Code)
	}
	rec := req(t, s, "POST", "/videos", `{"source_ref":"blob://media/BLOB-00000001"}`, "tech-1")
	if rec.Code != 201 {
		t.Fatalf("enqueue: %d %s", rec.Code, rec.Body)
	}
	var job VideoJob
	json.Unmarshal(rec.Body.Bytes(), &job)
	if job.Status != "queued" {
		t.Fatalf("bad status %q", job.Status)
	}
	// worker callback: running → done with manifest
	if rec := req(t, s, "POST", "/videos/"+job.ID+"/update", `{"status":"running"}`, "worker"); rec.Code != 200 {
		t.Fatalf("running: %d", rec.Code)
	}
	rec = req(t, s, "POST", "/videos/"+job.ID+"/update",
		`{"status":"done","manifest_ref":"blob://media/BLOB-00000009","segment_refs":["blob://media/BLOB-00000010"],"renditions":[720,480]}`, "worker")
	if rec.Code != 200 {
		t.Fatalf("done: %d %s", rec.Code, rec.Body)
	}
	json.Unmarshal(rec.Body.Bytes(), &job)
	if job.ManifestRef == "" || len(job.Renditions) != 2 {
		t.Fatalf("outputs missing: %+v", job)
	}
	// done without manifest refused
	rec2 := req(t, s, "POST", "/videos", `{"source_ref":"blob://media/BLOB-00000002"}`, "tech-1")
	var j2 VideoJob
	json.Unmarshal(rec2.Body.Bytes(), &j2)
	if rec := req(t, s, "POST", "/videos/"+j2.ID+"/update", `{"status":"done"}`, "worker"); rec.Code != 422 {
		t.Fatalf("done without manifest must fail: %d", rec.Code)
	}
	// illegal status
	if rec := req(t, s, "POST", "/videos/"+job.ID+"/update", `{"status":"teleported"}`, "worker"); rec.Code != 422 {
		t.Fatalf("illegal status: %d", rec.Code)
	}
}
