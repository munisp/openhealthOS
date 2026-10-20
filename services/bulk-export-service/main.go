// bulk-export-service — FHIR Bulk Data Access ($export) for the SHR
// (ADR-0054).
//
// The population-scale on-ramp: payers, kpiplane, the lakehouse and
// regulators ask "give me ALL Immunizations since January" — per-patient
// API reads are the wrong tool. This service implements the Bulk Data
// flow against the composed Medplum SHR:
//
//	GET  /fhir/$export?_type=Immunization&_since=2026-01-01T00:00:00Z
//	     → 202 + Content-Location: /fhir/bulkstatus/{job}
//	GET  /fhir/bulkstatus/{job}   → 202 while running; 200 + manifest when done
//	GET  /fhir/bulkfile/{job}/{type}.ndjson → the goods
//
// NDJSON is written to EXPORT_DIR (default ./bulk-spool) so files survive
// restarts; job state is in-memory (fail-soft: a restarted service reports
// unknown jobs as 410 Gone, never as phantom 200s). Auth seam: an inbound
// Bearer token is forwarded upstream untouched — SMART Backend Services
// client-credentials issuance lives at Keycloak, not here.
package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// Exportable types — keep the surface deliberate, not everything the SHR
// holds. Population-health pipelines need these; extend consciously.
var exportableTypes = map[string]bool{
	"Patient": true, "Encounter": true, "Observation": true,
	"Condition": true, "Immunization": true, "MedicationStatement": true,
	"AllergyIntolerance": true, "DocumentReference": true,
	"Claim": true, "Coverage": true, "Location": true, "Practitioner": true,
}

type Job struct {
	ID        string   `json:"id"`
	Types     []string `json:"types"`
	Since     string   `json:"since,omitempty"`
	Status    string   `json:"status"` // running | complete | failed
	CreatedAt string   `json:"created_at"`
	Error     string   `json:"error,omitempty"`
	Files     []string `json:"files,omitempty"` // {type}.ndjson relative names
}

type Service struct {
	mu       sync.Mutex
	jobs     map[string]*Job
	seq      int
	shrURL   string
	exportDir string
	client   *http.Client
	now      func() time.Time
}

func NewService(shrURL, exportDir string) *Service {
	return &Service{
		jobs:      map[string]*Job{},
		shrURL:    strings.TrimRight(shrURL, "/"),
		exportDir: exportDir,
		client:    &http.Client{Timeout: 60 * time.Second},
		now:       time.Now,
	}
}

var ErrBadType = errors.New("unsupported or missing _type")

// Kickoff validates and starts an export. Returns the job.
func (s *Service) Kickoff(typesParam, since string) (*Job, error) {
	types := []string{}
	if typesParam != "" {
		for _, t := range strings.Split(typesParam, ",") {
			t = strings.TrimSpace(t)
			if !exportableTypes[t] {
				return nil, fmt.Errorf("%w: %s", ErrBadType, t)
			}
			types = append(types, t)
		}
	} else {
		for t := range exportableTypes {
			types = append(types, t)
		}
	}
	sort.Strings(types)
	if since != "" {
		if _, err := time.Parse(time.RFC3339, since); err != nil {
			return nil, fmt.Errorf("_since must be RFC3339: %w", err)
		}
	}
	s.mu.Lock()
	s.seq++
	job := &Job{
		ID: fmt.Sprintf("EXP-%06d", s.seq), Types: types, Since: since,
		Status: "running", CreatedAt: s.now().UTC().Format(time.RFC3339),
	}
	s.jobs[job.ID] = job
	s.mu.Unlock()
	return job, nil
}

// Run executes the export synchronously (call in a goroutine from the HTTP
// layer; tests call it directly). Per-type fail-soft: a type that errors
// is skipped and recorded; the job only fails if EVERY type fails.
func (s *Service) Run(job *Job, authHeader string) {
	os.MkdirAll(filepath.Join(s.exportDir, job.ID), 0o755)
	var files []string
	var errs []string
	for _, t := range job.Types {
		n, err := s.exportType(job, t, authHeader)
		if err != nil {
			errs = append(errs, fmt.Sprintf("%s: %v", t, err))
			log.Printf("bulk-export %s: type %s failed: %v", job.ID, t, err)
			continue
		}
		if n > 0 {
			files = append(files, t+".ndjson")
		}
		log.Printf("bulk-export %s: %s → %d resources", job.ID, t, n)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	job.Files = files
	if len(errs) == len(job.Types) {
		job.Status = "failed"
		job.Error = strings.Join(errs, "; ")
		return
	}
	if len(errs) > 0 {
		job.Error = "partial: " + strings.Join(errs, "; ")
	}
	job.Status = "complete"
}

// exportType pages the SHR for one resource type and streams NDJSON to
// disk. Returns resource count.
func (s *Service) exportType(job *Job, rtype, authHeader string) (int, error) {
	if s.shrURL == "" {
		return 0, errors.New("SHR_URL unset")
	}
	path := filepath.Join(s.exportDir, job.ID, rtype+".ndjson")
	fh, err := os.Create(path)
	if err != nil {
		return 0, err
	}
	defer fh.Close()
	w := bufio.NewWriter(fh)
	defer w.Flush()

	count := 0
	next := fmt.Sprintf("%s/fhir/%s?_count=200", s.shrURL, rtype)
	if job.Since != "" {
		next += "&_lastUpdated=ge" + job.Since
	}
	for pages := 0; next != "" && pages < 500; pages++ {
		req, err := http.NewRequest(http.MethodGet, next, nil)
		if err != nil {
			return count, err
		}
		req.Header.Set("Accept", "application/fhir+json")
		if authHeader != "" {
			req.Header.Set("Authorization", authHeader)
		}
		resp, err := s.client.Do(req)
		if err != nil {
			return count, err
		}
		var bundle struct {
			ResourceType string `json:"resourceType"`
			Entry        []struct {
				Resource json.RawMessage `json:"resource"`
			} `json:"entry"`
			Link []struct {
				Relation string `json:"relation"`
				URL      string `json:"url"`
			} `json:"link"`
		}
		err = json.NewDecoder(resp.Body).Decode(&bundle)
		resp.Body.Close()
		if err != nil {
			return count, err
		}
		if resp.StatusCode != 200 || bundle.ResourceType != "Bundle" {
			os.Remove(path)
			return count, fmt.Errorf("SHR returned %d for %s", resp.StatusCode, rtype)
		}
		for _, e := range bundle.Entry {
			if len(e.Resource) == 0 {
				continue
			}
			w.Write(e.Resource)
			w.WriteByte('\n')
			count++
		}
		next = ""
		for _, l := range bundle.Link {
			if l.Relation == "next" {
				next = l.URL
			}
		}
	}
	if count == 0 {
		os.Remove(path) // no empty files in the manifest
	}
	return count, nil
}

func (s *Service) Get(id string) (*Job, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	j, ok := s.jobs[id]
	return j, ok
}

// ---------------------------------------------------------------- HTTP

func (s *Service) routes(mux *http.ServeMux) {
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]string{"status": "ok"})
	})
	mux.HandleFunc("GET /fhir/$export", s.kickoff)
	mux.HandleFunc("POST /fhir/$export", s.kickoff)
	mux.HandleFunc("GET /fhir/Patient/$export", s.kickoff) // all-patient variant
	mux.HandleFunc("GET /fhir/bulkstatus/{job}", s.status)
	mux.HandleFunc("GET /fhir/bulkfile/{job}/{file}", s.file)
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s *Service) kickoff(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	job, err := s.Kickoff(q.Get("_type"), q.Get("_since"))
	if err != nil {
		writeJSON(w, 400, map[string]string{"error": err.Error()})
		return
	}
	go s.Run(job, r.Header.Get("Authorization"))
	w.Header().Set("Content-Location", "/fhir/bulkstatus/"+job.ID)
	writeJSON(w, 202, map[string]string{"job": job.ID, "status": "running"})
}

func (s *Service) status(w http.ResponseWriter, r *http.Request) {
	job, ok := s.Get(r.PathValue("job"))
	if !ok {
		// Honest: unknown after restart is Gone, not a phantom 200.
		writeJSON(w, 410, map[string]string{"error": "job unknown (service restarted?) — re-kick the export"})
		return
	}
	if job.Status == "running" {
		w.Header().Set("X-Progress", "exporting")
		writeJSON(w, 202, map[string]string{"status": "running"})
		return
	}
	if job.Status == "failed" {
		writeJSON(w, 500, map[string]string{"status": "failed", "error": job.Error})
		return
	}
	out := map[string]any{
		"transactionTime":     job.CreatedAt,
		"requiresAccessToken": true,
		"status":              "complete",
	}
	if job.Error != "" {
		out["warning"] = job.Error
	}
	files := []map[string]string{}
	for _, f := range job.Files {
		t := strings.TrimSuffix(f, ".ndjson")
		files = append(files, map[string]string{
			"type": t, "url": "/fhir/bulkfile/" + job.ID + "/" + f,
		})
	}
	out["output"] = files
	writeJSON(w, 200, out)
}

func (s *Service) file(w http.ResponseWriter, r *http.Request) {
	job, file := r.PathValue("job"), r.PathValue("file")
	if _, ok := s.Get(job); !ok {
		writeJSON(w, 410, map[string]string{"error": "job unknown"})
		return
	}
	// Path containment: file must be a bare {Type}.ndjson name.
	if !strings.HasSuffix(file, ".ndjson") || strings.ContainsAny(file, "/\\") {
		writeJSON(w, 400, map[string]string{"error": "bad file name"})
		return
	}
	path := filepath.Join(s.exportDir, job, file)
	if !strings.HasPrefix(filepath.Clean(path), filepath.Clean(s.exportDir)+string(os.PathSeparator)) {
		writeJSON(w, 400, map[string]string{"error": "path escape rejected"})
		return
	}
	fh, err := os.Open(path)
	if err != nil {
		writeJSON(w, 404, map[string]string{"error": "no such export file"})
		return
	}
	defer fh.Close()
	w.Header().Set("Content-Type", "application/fhir+ndjson")
	io.Copy(w, fh)
}

func main() {
	listen := os.Getenv("LISTEN")
	if listen == "" {
		listen = ":8097"
	}
	dir := os.Getenv("EXPORT_DIR")
	if dir == "" {
		dir = "./bulk-spool"
	}
	s := NewService(os.Getenv("SHR_URL"), dir)
	mux := http.NewServeMux()
	s.routes(mux)
	log.Printf("bulk-export-service on %s (SHR=%s, dir=%s)", listen, s.shrURL, dir)
	if err := http.ListenAndServe(listen, mux); err != nil {
		panic(err)
	}
}
