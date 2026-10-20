package main

import (
	"bufio"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeSHR serves two paged Immunizations and one Patient.
func fakeSHR(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/fhir+json")
		switch {
		case strings.HasPrefix(r.URL.Path, "/fhir/Immunization"):
			if r.URL.Query().Get("_lastUpdated") != "" && !strings.HasPrefix(r.URL.Query().Get("_lastUpdated"), "ge") {
				t.Errorf("bad _since mapping: %s", r.URL.RawQuery)
			}
			json.NewEncoder(w).Encode(map[string]any{
				"resourceType": "Bundle",
				"entry": []any{
					map[string]any{"resource": map[string]any{"resourceType": "Immunization", "id": "I1"}},
					map[string]any{"resource": map[string]any{"resourceType": "Immunization", "id": "I2"}},
				},
			})
		case strings.HasPrefix(r.URL.Path, "/fhir/Patient"):
			json.NewEncoder(w).Encode(map[string]any{
				"resourceType": "Bundle",
				"entry": []any{map[string]any{"resource": map[string]any{
					"resourceType": "Patient", "id": "P1"}}},
			})
		default:
			json.NewEncoder(w).Encode(map[string]any{"resourceType": "Bundle", "entry": []any{}})
		}
	}))
}

func TestKickoffValidation(t *testing.T) {
	s := NewService("", t.TempDir())
	if _, err := s.Kickoff("Immunization,Patient", ""); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Kickoff("AuditEvent", ""); err == nil {
		t.Fatal("non-exportable type must be rejected")
	}
	if _, err := s.Kickoff("", "not-a-date"); err == nil {
		t.Fatal("bad _since must be rejected")
	}
	all, err := s.Kickoff("", "")
	if err != nil {
		t.Fatal(err)
	}
	if len(all.Types) != len(exportableTypes) {
		t.Fatalf("default export should cover all types, got %v", all.Types)
	}
}

func TestRunWritesNDJSON(t *testing.T) {
	shr := fakeSHR(t)
	defer shr.Close()
	dir := t.TempDir()
	s := NewService(shr.URL, dir)
	job, err := s.Kickoff("Immunization,Patient,Condition", "2026-01-01T00:00:00Z")
	if err != nil {
		t.Fatal(err)
	}
	s.Run(job, "")
	if job.Status != "complete" {
		t.Fatalf("job: %+v", job)
	}
	data, err := os.ReadFile(filepath.Join(dir, job.ID, "Immunization.ndjson"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Count(strings.TrimSpace(string(data)), "\n") + 1
	if lines != 2 {
		t.Fatalf("expected 2 NDJSON lines, got %d", lines)
	}
	if _, err := os.Stat(filepath.Join(dir, job.ID, "Patient.ndjson")); err != nil {
		t.Fatal("patient file missing")
	}
	// Condition was empty → file must not exist (no empty files in manifests)
	if _, err := os.Stat(filepath.Join(dir, job.ID, "Condition.ndjson")); !os.IsNotExist(err) {
		t.Fatal("empty type must not leave a file")
	}
	for _, f := range job.Files {
		if f == "Condition.ndjson" {
			t.Fatal("empty type must not appear in manifest")
		}
	}
}

func TestRunFailSoftPartial(t *testing.T) {
	// SHR that 500s on Immunization but serves Patient.
	shr := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "Immunization") {
			w.WriteHeader(500)
			return
		}
		json.NewEncoder(w).Encode(map[string]any{"resourceType": "Bundle",
			"entry": []any{map[string]any{"resource": map[string]any{"resourceType": "Patient", "id": "P1"}}}})
	}))
	defer shr.Close()
	s := NewService(shr.URL, t.TempDir())
	job, _ := s.Kickoff("Immunization,Patient", "")
	s.Run(job, "")
	if job.Status != "complete" || !strings.HasPrefix(job.Error, "partial:") {
		t.Fatalf("partial failure must complete with warning: %+v", job)
	}
}

func TestRunAllFailIsFailed(t *testing.T) {
	s := NewService("http://127.0.0.1:1", t.TempDir())
	job, _ := s.Kickoff("Patient", "")
	s.Run(job, "")
	if job.Status != "failed" {
		t.Fatalf("unreachable SHR must fail the job: %+v", job)
	}
}

func TestHTTPFlow(t *testing.T) {
	shr := fakeSHR(t)
	defer shr.Close()
	dir := t.TempDir()
	s := NewService(shr.URL, dir)
	mux := http.NewServeMux()
	s.routes(mux)
	ts := httptest.NewServer(mux)
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/fhir/$export?_type=Immunization,Patient")
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != 202 {
		t.Fatalf("kickoff: %d", resp.StatusCode)
	}
	loc := resp.Header.Get("Content-Location")
	resp.Body.Close()
	if loc == "" {
		t.Fatal("no Content-Location")
	}
	// poll until complete
	var manifest struct {
		Status string `json:"status"`
		Output []struct {
			Type string `json:"type"`
			URL  string `json:"url"`
		} `json:"output"`
	}
	for i := 0; i < 50; i++ {
		resp, err = http.Get(ts.URL + loc)
		if err != nil {
			t.Fatal(err)
		}
		if resp.StatusCode == 200 {
			json.NewDecoder(resp.Body).Decode(&manifest)
			resp.Body.Close()
			break
		}
		resp.Body.Close()
		time.Sleep(20 * time.Millisecond)
	}
	if manifest.Status != "complete" {
		t.Fatalf("manifest: %+v", manifest)
	}
	if len(manifest.Output) != 2 {
		t.Fatalf("expected 2 files, got %+v", manifest.Output)
	}
	resp, err = http.Get(ts.URL + manifest.Output[0].URL)
	if err != nil {
		t.Fatal(err)
	}
	if ct := resp.Header.Get("Content-Type"); ct != "application/fhir+ndjson" {
		t.Fatalf("content-type %s", ct)
	}
	sc := bufio.NewScanner(resp.Body)
	n := 0
	for sc.Scan() {
		n++
	}
	resp.Body.Close()
	if n == 0 {
		t.Fatal("empty ndjson served")
	}
}

func TestHTTPGuards(t *testing.T) {
	s := NewService("", t.TempDir())
	mux := http.NewServeMux()
	s.routes(mux)
	ts := httptest.NewServer(mux)
	defer ts.Close()

	resp, _ := http.Get(ts.URL + "/fhir/bulkstatus/EXP-999")
	if resp.StatusCode != 410 {
		t.Fatalf("unknown job must be 410, got %d", resp.StatusCode)
	}
	resp.Body.Close()
	resp, _ = http.Get(ts.URL + "/fhir/$export?_type=AuditEvent")
	if resp.StatusCode != 400 {
		t.Fatalf("bad type must be 400, got %d", resp.StatusCode)
	}
	resp.Body.Close()
	// path escape attempt
	job, _ := s.Kickoff("Patient", "")
	s.mu.Lock()
	s.jobs[job.ID].Status = "complete"
	s.mu.Unlock()
	req, _ := http.NewRequest("GET", ts.URL+"/fhir/bulkfile/"+job.ID+"/..%2f..%2fmain.go", nil)
	resp, err := http.DefaultClient.Do(req)
	if err == nil {
		if resp.StatusCode == 200 {
			t.Fatal("path escape must not serve files")
		}
		resp.Body.Close()
	}
}
