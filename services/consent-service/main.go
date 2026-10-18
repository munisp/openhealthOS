// Package main — OpenHealth OS consent manager (HIE-CM pattern, §5.4).
//
// Every cross-facility record share requires a signed, revocable consent
// artefact. Consent is data: issued with an HMAC signature over its
// canonical fields, revocable at any time, and every state change lands in
// an append-only audit log. NDPA 2023 baseline.
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// Consent is the artefact. The signature covers every field except the
// signature itself — tampering invalidates it.
type Consent struct {
	ID        string `json:"id"`
	PatientID string `json:"patient_id"`
	Grantee   string `json:"grantee"` // facility or provider registry id
	Scope     string `json:"scope"`   // e.g. "record:read", "claims:submit"
	Purpose   string `json:"purpose"` // NDPA lawful-basis label
	Status    string `json:"status"`  // active | revoked
	IssuedAt  string `json:"issued_at"`
	RevokedAt string `json:"revoked_at,omitempty"`
	Signature string `json:"signature"`
}

// AuditEntry is one immutable transition in the consent lifecycle.
type AuditEntry struct {
	TS        string `json:"ts"`
	Event     string `json:"event"` // ISSUE | REVOKE | CHECK
	ConsentID string `json:"consent_id"`
	Detail    string `json:"detail"`
}

var (
	ErrNotFound = errors.New("consent not found")
	ErrInvalid  = errors.New("invalid consent request: patient_id, grantee, scope and purpose are required")
	ErrTampered = errors.New("signature does not match artefact contents")
	ErrRevoked  = errors.New("consent is revoked")
)

// Manager issues, revokes and verifies consent artefacts.
type Manager struct {
	mu    sync.RWMutex
	key   []byte
	seq   int
	byID  map[string]*Consent
	audit []AuditEntry
	now   func() time.Time
}

func NewManager(key []byte) *Manager {
	return &Manager{key: key, byID: map[string]*Consent{}, now: time.Now}
}

// canonical returns the signed field set in a stable order.
func canonical(c *Consent) string {
	fields := []string{c.ID, c.PatientID, c.Grantee, c.Scope, c.Purpose, c.Status, c.IssuedAt, c.RevokedAt}
	return strings.Join(fields, "|")
}

func (m *Manager) sign(c *Consent) string { return signWith(m.key, c) }

// Verify recomputes the signature over the artefact's current contents.
func (m *Manager) Verify(c *Consent) bool {
	return hmac.Equal([]byte(c.Signature), []byte(m.sign(c)))
}

// signWith signs the canonical form with an explicit key — shared by the
// in-memory manager and the Postgres store so an artefact verifies
// identically on either side of a restart.
func signWith(key []byte, c *Consent) string {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(canonical(c)))
	return hex.EncodeToString(mac.Sum(nil))
}

func (m *Manager) record(event, id, detail string) {
	m.audit = append(m.audit, AuditEntry{
		TS: m.now().UTC().Format(time.RFC3339), Event: event, ConsentID: id, Detail: detail,
	})
}

// Issue creates and signs a new active consent artefact.
func (m *Manager) Issue(patientID, grantee, scope, purpose string) (Consent, error) {
	if patientID == "" || grantee == "" || scope == "" || purpose == "" {
		return Consent{}, ErrInvalid
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	c := &Consent{
		ID: fmt.Sprintf("CNS-%06d", m.seq), PatientID: patientID, Grantee: grantee,
		Scope: scope, Purpose: purpose, Status: "active",
		IssuedAt: m.now().UTC().Format(time.RFC3339),
	}
	c.Signature = m.sign(c)
	m.byID[c.ID] = c
	m.record("ISSUE", c.ID, fmt.Sprintf("patient=%s grantee=%s scope=%s", patientID, grantee, scope))
	return *c, nil
}

// Revoke marks an artefact revoked. Revocation is immediate and permanent.
func (m *Manager) Revoke(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	c, ok := m.byID[id]
	if !ok {
		return ErrNotFound
	}
	if c.Status == "revoked" {
		return nil // idempotent
	}
	c.Status = "revoked"
	c.RevokedAt = m.now().UTC().Format(time.RFC3339)
	c.Signature = m.sign(c)
	m.record("REVOKE", id, "")
	return nil
}

// ForPatient lists a patient's consent artefacts (newest first) — the
// portal's "who can see my record" view (ADR-0017).
func (m *Manager) ForPatient(patientID string) []Consent {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := []Consent{}
	for _, c := range m.byID {
		if c.PatientID == patientID {
			out = append(out, *c)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].IssuedAt != out[j].IssuedAt {
			return out[i].IssuedAt > out[j].IssuedAt
		}
		return out[i].ID > out[j].ID // sequential ids break same-second ties
	})
	return out
}

// Check answers the enforcement question: may grantee access patient's
// records under scope right now? Every check is audited.
func (m *Manager) Check(patientID, grantee, scope string) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	allowed := false
	for _, c := range m.byID {
		if c.PatientID == patientID && c.Grantee == grantee && c.Scope == scope && c.Status == "active" {
			if !m.Verify(c) {
				m.record("CHECK", c.ID, "DENIED: tampered artefact")
				return false, ErrTampered
			}
			allowed = true
			break
		}
	}
	detail := fmt.Sprintf("patient=%s grantee=%s scope=%s allowed=%v", patientID, grantee, scope, allowed)
	m.record("CHECK", "", detail)
	return allowed, nil
}

// Audit returns the append-only log (copy).
func (m *Manager) Audit() []AuditEntry {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]AuditEntry, len(m.audit))
	copy(out, m.audit)
	return out
}

// ---- HTTP surface ----

// ConsentService is the lifecycle seam: the in-memory Manager (edge,
// tests) and PostgresManager (state plane) both satisfy it.
type ConsentService interface {
	Issue(patientID, grantee, scope, purpose string) (Consent, error)
	Revoke(id string) error
	Check(patientID, grantee, scope string) (bool, error)
	Verify(c *Consent) bool
	Audit() []AuditEntry
	// ForPatient lists a patient's consent artefacts, newest first.
	ForPatient(patientID string) []Consent
}

type server struct{ m ConsentService }

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/consents", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			pid := r.URL.Query().Get("patient_id")
			if pid == "" {
				http.Error(w, "patient_id required", http.StatusBadRequest)
				return
			}
			writeJSON(w, 200, map[string]any{"consents": s.m.ForPatient(pid)})
			return
		}
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			PatientID string `json:"patient_id"`
			Grantee   string `json:"grantee"`
			Scope     string `json:"scope"`
			Purpose   string `json:"purpose"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
			return
		}
		c, err := s.m.Issue(req.PatientID, req.Grantee, req.Scope, req.Purpose)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, 201, c)
	})
	mux.HandleFunc("/consents/", func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/consents/")
		if strings.HasSuffix(path, "/revoke") && r.Method == http.MethodPost {
			id := strings.TrimSuffix(path, "/revoke")
			if err := s.m.Revoke(id); errors.Is(err, ErrNotFound) {
				http.Error(w, "not found", http.StatusNotFound)
				return
			}
			writeJSON(w, 200, map[string]string{"id": id, "status": "revoked"})
			return
		}
		http.Error(w, "not found", http.StatusNotFound)
	})
	mux.HandleFunc("/consents/check", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			PatientID string `json:"patient_id"`
			Grantee   string `json:"grantee"`
			Scope     string `json:"scope"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
			return
		}
		allowed, err := s.m.Check(req.PatientID, req.Grantee, req.Scope)
		if err != nil {
			writeJSON(w, 200, map[string]any{"allowed": false, "reason": err.Error()})
			return
		}
		writeJSON(w, 200, map[string]any{"allowed": allowed})
	})
	mux.HandleFunc("/audit", func(w http.ResponseWriter, r *http.Request) {
		audit := s.m.Audit()
		sort.Slice(audit, func(i, j int) bool { return i < j }) // insertion order
		writeJSON(w, 200, audit)
	})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}
