// Package main — OpenHealth OS shared health record (SHR) service.
//
// FHIR R4 resource store with consent enforcement on cross-facility
// reads (HIE-CM pattern, ADR-0002/0004). The rule is simple and
// structural:
//
//	same-facility read     → allowed (treatment at source)
//	cross-facility read    → requires an active consent artefact
//	break-glass emergency  → allowed, flagged, and audited loudly
//
// Every read is an audit event with its legal basis. A record whose
// access cannot be attributed is a breach waiting to be discovered.
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Resource is one stored FHIR R4 resource with its provenance.
type Resource struct {
	ResourceType   string          `json:"resourceType"`
	ID             string          `json:"id"`
	PatientID      string          `json:"patient_id"`     // subject of the record
	SourceFacility string          `json:"source_facility"` // where it was created
	Body           json.RawMessage `json:"body"`
	UpdatedAt      string          `json:"updated_at"`
}

// AuditEntry records one access decision with its basis.
type AuditEntry struct {
	At         string `json:"at"`
	Actor      string `json:"actor"`       // requesting facility / user
	PatientID  string `json:"patient_id"`
	Resource   string `json:"resource"`    // e.g. Observation/obs-1
	Decision   string `json:"decision"`    // allowed | denied | break-glass
	Basis      string `json:"basis"`       // same-facility | consent | emergency | no-consent
}

var (
	ErrNotFound   = errors.New("resource not found")
	ErrNoConsent  = errors.New("cross-facility read requires an active consent artefact")
	ErrBadRequest = errors.New("resourceType, id, patient_id and source_facility required")
)

// ConsentChecker is the seam to the consent service (HIE-CM). The static
// fixture and the HTTP client below both satisfy it.
type ConsentChecker interface {
	Check(patientID, grantee, scope string) (bool, error)
}

// HTTPConsentChecker calls the consent service /consents/check.
type HTTPConsentChecker struct{ BaseURL string }

func (c HTTPConsentChecker) Check(patientID, grantee, scope string) (bool, error) {
	body, _ := json.Marshal(map[string]string{
		"patient_id": patientID, "grantee": grantee, "scope": scope,
	})
	resp, err := http.Post(c.BaseURL+"/consents/check", "application/json", bytes.NewReader(body))
	if err != nil {
		return false, fmt.Errorf("consent service unreachable: %w", err)
	}
	defer resp.Body.Close()
	var out struct {
		Allowed bool `json:"allowed"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return false, err
	}
	return out.Allowed, nil
}

// Store keeps resources and the access audit log.
type Store struct {
	mu        sync.Mutex
	resources map[string]*Resource // key: resourceType/id
	audit     []AuditEntry
	consent   ConsentChecker
	now       func() time.Time
}

func NewStore(consent ConsentChecker) *Store {
	return &Store{
		resources: map[string]*Resource{},
		consent:   consent,
		now:       func() time.Time { return time.Now().UTC() },
	}
}

func key(rt, id string) string { return rt + "/" + id }

// Put upserts a resource. Writes are source-facility events replicated
// upward — consent governs reads, not writes.
func (s *Store) Put(r Resource) error {
	if r.ResourceType == "" || r.ID == "" || r.PatientID == "" || r.SourceFacility == "" {
		return ErrBadRequest
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	r.UpdatedAt = s.now().Format(time.RFC3339)
	stored := r
	s.resources[key(r.ResourceType, r.ID)] = &stored
	return nil
}

// Get enforces the consent rule on a single-resource read.
func (s *Store) Get(rt, id, actorFacility string, breakGlass bool) (*Resource, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	r, ok := s.resources[key(rt, id)]
	if !ok {
		return nil, ErrNotFound
	}
	if err := s.authorizeLocked(r, actorFacility, breakGlass); err != nil {
		return nil, err
	}
	return r, nil
}

// PatientEverything returns a patient's record set under the same rule —
// one authorization decision covers the bundle, and the audit entry
// names the bundle.
func (s *Store) PatientEverything(patientID, actorFacility string, breakGlass bool) ([]*Resource, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []*Resource
	for _, r := range s.resources {
		if r.PatientID == patientID {
			out = append(out, r)
		}
	}
	if len(out) == 0 {
		return nil, ErrNotFound
	}
	probe := out[0]
	probeCopy := *probe
	probeCopy.ID = "*" // audit reads as Patient-bundle
	if err := s.authorizeLocked(&probeCopy, actorFacility, breakGlass); err != nil {
		return nil, err
	}
	return out, nil
}

// authorizeLocked applies the access rule and writes the audit entry.
// Caller holds s.mu.
func (s *Store) authorizeLocked(r *Resource, actorFacility string, breakGlass bool) error {
	entry := AuditEntry{
		At: s.now().Format(time.RFC3339), Actor: actorFacility,
		PatientID: r.PatientID, Resource: key(r.ResourceType, r.ID),
	}
	switch {
	case actorFacility == r.SourceFacility:
		entry.Decision, entry.Basis = "allowed", "same-facility"
	case breakGlass:
		// Emergency access: allowed, but the audit trail names it
		// break-glass — reviewable, never silent.
		entry.Decision, entry.Basis = "break-glass", "emergency"
	default:
		allowed, err := s.consent.Check(r.PatientID, actorFacility, "shr-read")
		entry.Basis = "consent"
		if err != nil || !allowed {
			entry.Decision, entry.Basis = "denied", "no-consent"
			s.audit = append(s.audit, entry)
			return ErrNoConsent
		}
		entry.Decision = "allowed"
	}
	s.audit = append(s.audit, entry)
	return nil
}

// Audit returns the access log — every decision, with its basis.
func (s *Store) Audit() []AuditEntry {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]AuditEntry, len(s.audit))
	copy(out, s.audit)
	return out
}

// ---------------------------------------------------------------- HTTP

type server struct{ store *Store }

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

// accessContext extracts the requester's facility and break-glass flag.
func accessContext(r *http.Request) (facility string, breakGlass bool) {
	return r.Header.Get("X-Facility-ID"), r.Header.Get("X-Break-Glass") == "true"
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	// PUT /fhir/{type}/{id} — upsert a resource (source facility only;
	// provenance is asserted in the body, verified upstream).
	mux.HandleFunc("/fhir/", func(w http.ResponseWriter, r *http.Request) {
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/fhir/"), "/")
		switch {
		case r.Method == http.MethodPut && len(parts) == 2:
			s.putResource(w, r, parts[0], parts[1])
		case r.Method == http.MethodGet && len(parts) == 2:
			s.getResource(w, r, parts[0], parts[1])
		default:
			http.Error(w, "unsupported", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/fhir/Patient/", func(w http.ResponseWriter, r *http.Request) {
		// GET /fhir/Patient/{id}/$everything
		parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/fhir/Patient/"), "/")
		if r.Method == http.MethodGet && len(parts) == 2 && parts[1] == "$everything" {
			s.patientEverything(w, r, parts[0])
			return
		}
		http.Error(w, "unsupported", http.StatusMethodNotAllowed)
	})
	mux.HandleFunc("/audit", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, s.store.Audit())
	})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

func (s server) putResource(w http.ResponseWriter, r *http.Request, rt, id string) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read body: "+err.Error(), http.StatusBadRequest)
		return
	}
	var res Resource
	if err := json.Unmarshal(body, &res); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}
	res.ResourceType, res.ID = rt, id // path is authoritative
	if res.Body == nil {
		res.Body = body
	}
	if err := s.store.Put(res); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	writeJSON(w, 200, map[string]string{"status": "stored", "resource": key(rt, id)})
}

func (s server) getResource(w http.ResponseWriter, r *http.Request, rt, id string) {
	facility, breakGlass := accessContext(r)
	if facility == "" {
		http.Error(w, "X-Facility-ID header required — unattributed access is never allowed", http.StatusBadRequest)
		return
	}
	res, err := s.store.Get(rt, id, facility, breakGlass)
	if err != nil {
		switch {
		case errors.Is(err, ErrNotFound):
			http.Error(w, err.Error(), http.StatusNotFound)
		case errors.Is(err, ErrNoConsent):
			http.Error(w, err.Error(), http.StatusForbidden)
		default:
			http.Error(w, err.Error(), http.StatusInternalServerError)
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write(res.Body)
}

func (s server) patientEverything(w http.ResponseWriter, r *http.Request, patientID string) {
	facility, breakGlass := accessContext(r)
	if facility == "" {
		http.Error(w, "X-Facility-ID header required", http.StatusBadRequest)
		return
	}
	resources, err := s.store.PatientEverything(patientID, facility, breakGlass)
	if err != nil {
		switch {
		case errors.Is(err, ErrNotFound):
			http.Error(w, err.Error(), http.StatusNotFound)
		case errors.Is(err, ErrNoConsent):
			http.Error(w, err.Error(), http.StatusForbidden)
		default:
			http.Error(w, err.Error(), http.StatusInternalServerError)
		}
		return
	}
	entries := make([]map[string]any, 0, len(resources))
	for _, res := range resources {
		var body any
		json.Unmarshal(res.Body, &body)
		entries = append(entries, map[string]any{"resource": body})
	}
	writeJSON(w, 200, map[string]any{
		"resourceType": "Bundle", "type": "searchset",
		"total": len(entries), "entry": entries,
	})
}
