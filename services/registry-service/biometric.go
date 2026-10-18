// biometric.go — biometric identity assurance (ADR-0017).
//
// Why: identity gaming in Nigerian coverage programs is concrete —
// one person enrols twice under two names to draw two benefit packages;
// a card (or phone) is lent to an ineligible relative. Demographics
// cannot stop either; a body can.
//
// What ships here:
//   - a Matcher seam with a deterministic simulated matcher (normalized
//     Hamming over template bytes). Production swaps in a licensed AFIS
//     behind the same interface — the platform never embeds a
//     proprietary biometric SDK (ADR-0005 doctrine applies to biometrics
//     too). The simulation is honest: it exists so every flow — dedupe,
//     verify, ladder upgrade — is testable end to end today.
//   - enrol / verify / identify flows with templates sealed at rest
//     (sealx keyring — biometric data is the most sensitive PII the
//     platform holds).
//   - the anti-gaming rule: enrolment runs a 1:N identify first. A body
//     that already belongs to another patient is a duplicate identity —
//     refused loudly (409), audited via the KYC detail.
//   - the KYC ladder gains biometric-verified, above nin-verified.
//
// What this is NOT: liveness detection or presentation-attack defense.
// Those are capture-device properties (the scanner at the desk), called
// out honestly in the ADR — software cannot stop a gummy finger, the
// device can.
package main

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/munisp/openhealthOS/shared/sealx"
)

// KYCBiometricVerified tops the ladder: a live match against an enrolled
// template is stronger evidence than an authority record lookup.
const KYCBiometricVerified = "biometric-verified"

// ---------------------------------------------------------------- matcher

// Matcher is the AFIS seam. Similarity in [0,1]; thresholding is the
// caller's policy, not the matcher's.
type Matcher interface {
	Similarity(a, b []byte) float64
}

// SimMatcher is the deterministic development/test matcher: normalized
// Hamming similarity over the raw template bytes (lengths reconciled by
// truncation to the shorter — templates are fixed-length in practice).
type SimMatcher struct{}

func (SimMatcher) Similarity(a, b []byte) float64 {
	if len(a) == 0 || len(b) == 0 {
		return 0
	}
	n := len(a)
	if len(b) < n {
		n = len(b)
	}
	same := 0
	for i := 0; i < n; i++ {
		x := a[i] ^ b[i]
		same += 8 - bits(x)
	}
	return float64(same) / float64(n*8)
}

func bits(x byte) int {
	n := 0
	for x != 0 {
		n += int(x & 1)
		x >>= 1
	}
	return n
}

// matchThreshold reads BIOMETRIC_THRESHOLD (default 0.85). One policy
// knob, env-driven, identical edge and state.
func matchThreshold(getenv func(string) string) float64 {
	if v := getenv("BIOMETRIC_THRESHOLD"); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil && f > 0 && f <= 1 {
			return f
		}
	}
	return 0.85
}

// ---------------------------------------------------------------- store

// EnrolledTemplate is one finger's template for one patient, sealed.
type EnrolledTemplate struct {
	PatientID string `json:"patient_id"`
	Finger    string `json:"finger"` // e.g. "right-index"
	Sealed    string `json:"sealed"` // sealx token (or loud plaintext in unprovisioned dev)
	CreatedAt string `json:"created_at"`
}

// BiometricStore is the persistence seam (memory edge / Postgres state).
type BiometricStore interface {
	Enrol(t EnrolledTemplate) error
	ForPatient(patientID string) []EnrolledTemplate
	All() []EnrolledTemplate
}

type MemoryBiometricStore struct {
	mu   sync.Mutex
	rows []EnrolledTemplate
}

func NewMemoryBiometricStore() *MemoryBiometricStore { return &MemoryBiometricStore{} }

func (m *MemoryBiometricStore) Enrol(t EnrolledTemplate) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, r := range m.rows {
		if r.PatientID == t.PatientID && r.Finger == t.Finger {
			return nil // re-enrolling the same finger is idempotent
		}
	}
	m.rows = append(m.rows, t)
	return nil
}

func (m *MemoryBiometricStore) ForPatient(patientID string) []EnrolledTemplate {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []EnrolledTemplate{}
	for _, r := range m.rows {
		if r.PatientID == patientID {
			out = append(out, r)
		}
	}
	return out
}

func (m *MemoryBiometricStore) All() []EnrolledTemplate {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]EnrolledTemplate, len(m.rows))
	copy(out, m.rows)
	return out
}

// ---------------------------------------------------------------- engine

// BiometricEngine wires matcher + store + sealer + the patient store
// (for the ladder upgrade).
type BiometricEngine struct {
	Store     BiometricStore
	Matcher   Matcher
	Sealer    *sealx.Sealer // nil = unprovisioned dev — templates stay plaintext, loudly
	Patients  PatientStore
	Threshold float64
	now       func() time.Time
}

func NewBiometricEngine(store BiometricStore, patients PatientStore, sealer *sealx.Sealer, getenv func(string) string) *BiometricEngine {
	return &BiometricEngine{
		Store: store, Matcher: SimMatcher{}, Patients: patients, Sealer: sealer,
		Threshold: matchThreshold(getenv), now: time.Now,
	}
}

// sealTemplate seals at rest when provisioned; the dev-mode plaintext is
// prefixed so nobody mistakes it for protection.
func (e *BiometricEngine) sealTemplate(raw []byte) string {
	if e.Sealer == nil {
		return "PLAIN:" + hex.EncodeToString(raw)
	}
	return e.Sealer.Seal(raw)
}

func (e *BiometricEngine) openTemplate(stored string) ([]byte, error) {
	if len(stored) > 6 && stored[:6] == "PLAIN:" {
		return hex.DecodeString(stored[6:])
	}
	return e.Sealer.Open(stored)
}

type identifyHit struct {
	PatientID string  `json:"patient_id"`
	Finger    string  `json:"finger"`
	Score     float64 `json:"score"`
}

// identify runs the probe template against everything enrolled.
func (e *BiometricEngine) identify(probe []byte) []identifyHit {
	hits := []identifyHit{}
	for _, row := range e.Store.All() {
		tpl, err := e.openTemplate(row.Sealed)
		if err != nil {
			continue // an unopenable template is skipped, never matched
		}
		if score := e.Matcher.Similarity(probe, tpl); score >= e.Threshold {
			hits = append(hits, identifyHit{PatientID: row.PatientID, Finger: row.Finger, Score: score})
		}
	}
	sort.Slice(hits, func(i, j int) bool { return hits[i].Score > hits[j].Score })
	return hits
}

var errDuplicateIdentity = &bioError{"duplicate identity: this biometric is already enrolled to another patient"}

type bioError struct{ msg string }

func (e *bioError) Error() string { return e.msg }

// EnrolBiometric stores one finger's template after the 1:N dedupe gate.
func (e *BiometricEngine) EnrolBiometric(patientID, finger string, template []byte) error {
	if _, err := e.Patients.GetPatient(patientID); err != nil {
		return err
	}
	for _, h := range e.identify(template) {
		if h.PatientID != patientID {
			return errDuplicateIdentity
		}
	}
	return e.Store.Enrol(EnrolledTemplate{
		PatientID: patientID, Finger: finger,
		Sealed:    e.sealTemplate(template),
		CreatedAt: e.now().UTC().Format(time.RFC3339),
	})
}

// VerifyBiometric matches 1:1 against the patient's own templates; a
// pass raises the ladder to biometric-verified.
func (e *BiometricEngine) VerifyBiometric(patientID string, template []byte) (float64, bool, error) {
	best := 0.0
	for _, row := range e.Store.ForPatient(patientID) {
		tpl, err := e.openTemplate(row.Sealed)
		if err != nil {
			continue
		}
		if s := e.Matcher.Similarity(template, tpl); s > best {
			best = s
		}
	}
	if len(e.Store.ForPatient(patientID)) == 0 {
		return 0, false, errNotEnrolled
	}
	if best >= e.Threshold {
		if e.Patients != nil {
			e.Patients.SetKYC(patientID, KYCBiometricVerified,
				"biometric match (score "+strconv.FormatFloat(best, 'f', 3, 64)+")")
		}
		return best, true, nil
	}
	return best, false, nil
}

var errNotEnrolled = &bioError{"no biometric enrolled for this patient"}

// ---------------------------------------------------------------- http

func registerBiometricRoutes(mux *http.ServeMux, e *BiometricEngine) {
	mux.HandleFunc("POST /kyc/biometric/enrol", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
			Finger    string `json:"finger"`
			Template  string `json:"template"` // hex
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil ||
			req.PatientID == "" || req.Finger == "" || req.Template == "" {
			http.Error(w, `{"error":"patient_id, finger and template required"}`, http.StatusBadRequest)
			return
		}
		raw, err := hex.DecodeString(req.Template)
		if err != nil || len(raw) < 16 {
			http.Error(w, `{"error":"template must be hex, at least 16 bytes"}`, http.StatusBadRequest)
			return
		}
		if err := e.EnrolBiometric(req.PatientID, req.Finger, raw); err != nil {
			if err == errDuplicateIdentity {
				writeBioErr(w, http.StatusConflict, err)
				return
			}
			if errors.Is(err, ErrNotFound) {
				writeBioErr(w, http.StatusNotFound, errNotFoundPatient)
				return
			}
			writeBioErr(w, http.StatusBadRequest, err)
			return
		}
		writeJSON(w, http.StatusCreated, map[string]any{"enrolled": true, "patient_id": req.PatientID, "finger": req.Finger})
	})

	mux.HandleFunc("POST /kyc/biometric/verify", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
			Template  string `json:"template"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil ||
			req.PatientID == "" || req.Template == "" {
			http.Error(w, `{"error":"patient_id and template required"}`, http.StatusBadRequest)
			return
		}
		raw, err := hex.DecodeString(req.Template)
		if err != nil {
			http.Error(w, `{"error":"template must be hex"}`, http.StatusBadRequest)
			return
		}
		score, ok, err := e.VerifyBiometric(req.PatientID, raw)
		if err == errNotEnrolled {
			writeBioErr(w, http.StatusNotFound, err)
			return
		}
		writeJSON(w, 200, map[string]any{"match": ok, "score": score, "patient_id": req.PatientID})
	})

	mux.HandleFunc("POST /kyc/biometric/identify", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Template string `json:"template"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Template == "" {
			http.Error(w, `{"error":"template required"}`, http.StatusBadRequest)
			return
		}
		raw, err := hex.DecodeString(req.Template)
		if err != nil {
			http.Error(w, `{"error":"template must be hex"}`, http.StatusBadRequest)
			return
		}
		writeJSON(w, 200, map[string]any{"candidates": e.identify(raw)})
	})
}

func writeBioErr(w http.ResponseWriter, code int, err error) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
}

var errNotFoundPatient = &bioError{"patient not found"}

// getenv indirection for tests.
var bioGetenv = os.Getenv
