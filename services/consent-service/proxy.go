// proxy.go — caregiver/proxy delegated access (ADR-0056). Children, elderly
// and incapacitated patients cannot hold their own sessions; a registered
// proxy (parent, guardian, next-of-kin) acts for them under a scoped,
// signed, revocable grant with the same audit discipline as Consent.
// Doctrine: "consent:manage" (a proxy granting further proxies) is refused —
// delegation is one level. In-memory manager ships now; the Postgres seam
// (ProxyService interface) lets deployers persist without code changes here.
package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"
)

type ProxyGrant struct {
	ID           string `json:"id"`
	PatientID    string `json:"patient_id"`
	ProxyID      string `json:"proxy_id"`     // registry id of the caregiver
	Relationship string `json:"relationship"` // parent | guardian | spouse | child | next-of-kin
	Scope        string `json:"scope"`        // comma-separated rights
	ExpiresAt    string `json:"expires_at"`
	Status       string `json:"status"` // active | revoked
	IssuedAt     string `json:"issued_at"`
	Signature    string `json:"signature"`
}

var (
	ErrProxyInvalid = errors.New("invalid proxy grant: patient_id, proxy_id, relationship, scope, expires_at required")
	ErrDelegation   = errors.New("proxy grants cannot include consent:manage — delegation is one level")
)

type ProxyService interface {
	IssueProxy(patientID, proxyID, relationship, scope string, expiresAt time.Time) (ProxyGrant, error)
	CheckProxy(patientID, proxyID, right string) (bool, error)
	RevokeProxy(id string) error
	ForProxyPatient(patientID string) []ProxyGrant
	ProxyAudit() []AuditEntry
}

type ProxyManager struct {
	mu    sync.RWMutex
	key   []byte
	seq   int
	byID  map[string]*ProxyGrant
	audit []AuditEntry
	now   func() time.Time
}

func NewProxyManager(key []byte) *ProxyManager {
	return &ProxyManager{key: key, byID: map[string]*ProxyGrant{}, now: time.Now}
}

func (m *ProxyManager) sign(g *ProxyGrant) string {
	mac := hmac.New(sha256.New, m.key)
	mac.Write([]byte(strings.Join([]string{g.ID, g.PatientID, g.ProxyID, g.Relationship,
		g.Scope, g.ExpiresAt, g.Status, g.IssuedAt}, "|")))
	return fmt.Sprintf("%x", mac.Sum(nil))
}

func (m *ProxyManager) IssueProxy(patientID, proxyID, relationship, scope string, expiresAt time.Time) (ProxyGrant, error) {
	if patientID == "" || proxyID == "" || relationship == "" || scope == "" || expiresAt.IsZero() {
		return ProxyGrant{}, ErrProxyInvalid
	}
	for _, s := range strings.Split(scope, ",") {
		if strings.TrimSpace(s) == "consent:manage" {
			return ProxyGrant{}, ErrDelegation
		}
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	g := &ProxyGrant{
		ID: fmt.Sprintf("PX-%06d", m.seq), PatientID: patientID, ProxyID: proxyID,
		Relationship: relationship, Scope: scope, ExpiresAt: expiresAt.UTC().Format(time.RFC3339),
		Status: "active", IssuedAt: m.now().UTC().Format(time.RFC3339),
	}
	g.Signature = m.sign(g)
	m.byID[g.ID] = g
	m.audit = append(m.audit, AuditEntry{TS: m.now().UTC().Format(time.RFC3339),
		Event: "PROXY_ISSUE", ConsentID: g.ID, Detail: proxyID + " -> " + patientID + " (" + scope + ")"})
	return *g, nil
}

func (m *ProxyManager) CheckProxy(patientID, proxyID, right string) (bool, error) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	for _, g := range m.byID {
		if g.PatientID != patientID || g.ProxyID != proxyID {
			continue
		}
		if g.Status != "active" {
			return false, ErrRevoked
		}
		exp, _ := time.Parse(time.RFC3339, g.ExpiresAt)
		if !m.now().Before(exp) {
			return false, ErrRevoked
		}
		if !hmac.Equal([]byte(m.sign(g)), []byte(g.Signature)) {
			return false, ErrTampered
		}
		for _, s := range strings.Split(g.Scope, ",") {
			if strings.TrimSpace(s) == right {
				return true, nil
			}
		}
		return false, nil
	}
	return false, ErrNotFound
}

func (m *ProxyManager) RevokeProxy(id string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	g, ok := m.byID[id]
	if !ok {
		return ErrNotFound
	}
	g.Status = "revoked"
	g.Signature = m.sign(g)
	m.audit = append(m.audit, AuditEntry{TS: m.now().UTC().Format(time.RFC3339),
		Event: "PROXY_REVOKE", ConsentID: id})
	return nil
}

func (m *ProxyManager) ForProxyPatient(patientID string) []ProxyGrant {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := []ProxyGrant{}
	for _, g := range m.byID {
		if g.PatientID == patientID {
			out = append(out, *g)
		}
	}
	return out
}

func (m *ProxyManager) ProxyAudit() []AuditEntry {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make([]AuditEntry, len(m.audit))
	copy(out, m.audit)
	return out
}

var _ ProxyService = (*ProxyManager)(nil)

type proxyServer struct{ m ProxyService }

func (s proxyServer) routes(mux *http.ServeMux) {
	mux.HandleFunc("POST /proxy", func(w http.ResponseWriter, r *http.Request) {
		var in struct {
			PatientID    string `json:"patient_id"`
			ProxyID      string `json:"proxy_id"`
			Relationship string `json:"relationship"`
			Scope        string `json:"scope"`
			ExpiresAt    string `json:"expires_at"`
		}
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			writeJSON(w, 400, map[string]string{"error": "bad json"})
			return
		}
		exp, err := time.Parse(time.RFC3339, in.ExpiresAt)
		if err != nil {
			exp = time.Now().Add(365 * 24 * time.Hour)
		}
		g, err := s.m.IssueProxy(in.PatientID, in.ProxyID, in.Relationship, in.Scope, exp)
		if err != nil {
			writeJSON(w, 422, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, 201, g)
	})
	mux.HandleFunc("POST /proxy/check", func(w http.ResponseWriter, r *http.Request) {
		var in struct {
			PatientID string `json:"patient_id"`
			ProxyID   string `json:"proxy_id"`
			Right     string `json:"right"`
		}
		if err := json.NewDecoder(r.Body).Decode(&in); err != nil {
			writeJSON(w, 400, map[string]string{"error": "bad json"})
			return
		}
		ok, err := s.m.CheckProxy(in.PatientID, in.ProxyID, in.Right)
		resp := map[string]any{"allowed": ok}
		if err != nil && !errors.Is(err, ErrNotFound) {
			resp["reason"] = err.Error()
		}
		writeJSON(w, 200, resp)
	})
	mux.HandleFunc("GET /proxy", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, s.m.ForProxyPatient(r.URL.Query().Get("patient_id")))
	})
	mux.HandleFunc("DELETE /proxy/{id}", func(w http.ResponseWriter, r *http.Request) {
		if err := s.m.RevokeProxy(r.PathValue("id")); err != nil {
			writeJSON(w, 404, map[string]string{"error": "not found"})
			return
		}
		writeJSON(w, 200, map[string]string{"status": "revoked"})
	})
}
