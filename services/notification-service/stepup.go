// stepup.go — random / step-up re-authentication at the PHI boundary
// (extends ADR-0016/0017).
//
// Doctrine: browsing menus and receiving "your result is ready" nudges is
// low-risk; RENDERING clinical detail is not. So the portal rail splits
// endpoints into two classes:
//
//	open      — coverage, facilities, slots, feed metadata (no PHI detail)
//	elevated  — /portal/record and anything carrying clinical content
//
// Elevated endpoints require a step-up token, earned by a fresh challenge:
// a 6-digit code delivered over the patient's OWN channel (the provider
// chain — matrix leg first when bound, SMS otherwise). Codes are stored as
// SHA-256 hashes, compared in constant time, expire in 10 minutes, allow 5
// attempts, and burn on success. Tokens live 10 minutes.
//
// Random re-authentication: even inside a valid token window, every PHI
// read has STEPPUP_RANDOM_PERCENT chance (default 15%) of being bounced
// with 428 step_up_required, forcing a fresh challenge. An attacker holding
// a stolen session can never COUNT on continued access — the ground moves
// under them at random, and every bounce is audited.
//
// Fail-closed everywhere: unknown patient/phone → 404 indistinguishable;
// wrong code → 404; expired → 404; nothing reveals which half failed.
package main

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	mrand "math/rand"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"
)

const (
	stepUpCodeTTL   = 10 * time.Minute
	stepUpTokenTTL  = 10 * time.Minute
	stepUpMaxTries  = 5
	stepUpChallenge = "step_up_required"
)

type stepUpChallenge_ struct {
	hash     [32]byte
	expires  time.Time
	attempts int
}

type stepUpToken struct {
	expires time.Time
}

// stepUp is the gate state machine, riding the Service for store+provider.
type stepUp struct {
	mu         sync.Mutex
	challenges map[string]*stepUpChallenge_ // patientID -> active challenge
	tokens     map[[32]byte]stepUpToken     // sha256(token) -> expiry
	randomPct  int                          // 0..100
	rnd        *mrand.Rand                  // injectable for tests
}

func newStepUp() *stepUp {
	pct := 15
	if v := os.Getenv("STEPPUP_RANDOM_PERCENT"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n >= 0 && n <= 100 {
			pct = n
		}
	}
	return &stepUp{
		challenges: map[string]*stepUpChallenge_{},
		tokens:     map[[32]byte]stepUpToken{},
		randomPct:  pct,
		rnd:        mrand.New(mrand.NewSource(time.Now().UnixNano())),
	}
}

func hashStep(s string) [32]byte { return sha256.Sum256([]byte(s)) }

// issue creates a code, stores only its hash, returns the code for delivery.
func (g *stepUp) issue(patientID string) string {
	b := make([]byte, 4)
	_, _ = rand.Read(b)
	code := fmt.Sprintf("%06d", (int(b[0])<<16|int(b[1])<<8|int(b[2]))%1000000)
	g.mu.Lock()
	defer g.mu.Unlock()
	g.challenges[patientID] = &stepUpChallenge_{
		hash: hashStep(code), expires: time.Now().Add(stepUpCodeTTL),
	}
	return code
}

// verify checks a code: constant-time, attempt-bounded, burn-on-success.
// On success returns a fresh elevated token.
func (g *stepUp) verify(patientID, code string) (string, bool) {
	g.mu.Lock()
	defer g.mu.Unlock()
	c, ok := g.challenges[patientID]
	if !ok || time.Now().After(c.expires) {
		return "", false
	}
	c.attempts++
	got := hashStep(code)
	if subtle.ConstantTimeCompare(got[:], c.hash[:]) != 1 {
		if c.attempts >= stepUpMaxTries {
			delete(g.challenges, patientID) // brute-force kill: re-issue required
		}
		return "", false
	}
	delete(g.challenges, patientID) // burned
	raw := make([]byte, 32)
	_, _ = rand.Read(raw)
	tok := base64.RawURLEncoding.EncodeToString(raw)
	g.tokens[hashStep(tok)] = stepUpToken{expires: time.Now().Add(stepUpTokenTTL)}
	return tok, true
}

// elevated reports whether a PHI read may proceed. A valid token can still
// be bounced at random — that is the point.
func (g *stepUp) elevated(patientID, token string) (bool, string) {
	g.mu.Lock()
	defer g.mu.Unlock()
	t, ok := g.tokens[hashStep(token)]
	if !ok || time.Now().After(t.expires) {
		return false, "no_elevation"
	}
	if g.randomPct > 0 && g.rnd.Intn(100) < g.randomPct {
		return false, "random_reauth"
	}
	return true, ""
}

// auditStepUp emits one structured line per gate event — the ops lake
// ingests service logs, so challenges, failures, and random bounces are
// all forensically visible without a new store.
func auditStepUp(event, patientID, detail string) {
	log.Printf("audit stepup %s patient=%s detail=%q", event, patientID, detail)
}

// --- HTTP surface ---------------------------------------------------------------

// registerStepUp wires the challenge/verify endpoints.
func (s server) registerStepUp(mux *http.ServeMux) {
	mux.HandleFunc("POST /portal/stepup/challenge", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
			Phone     string `json:"phone"`
		}
		if !decode(w, r, &req) {
			return
		}
		sub, err := s.svc.store.Subscription(req.PatientID)
		if err != nil || sub.Phone != NormalizePhone(req.Phone) || req.PatientID == "" || req.Phone == "" {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		code := s.svc.stepup.issue(req.PatientID)
		// Deliver over the patient's own rail: matrix leg when bound, SMS
		// otherwise — the same provider chain, with its breakers.
		if err := s.svc.provider.Send(sub.Phone,
			"OpenHealth security code: "+code+" (valid 10 minutes — staff will never ask for it)"); err != nil {
			auditStepUp("challenge_undelivered", req.PatientID, err.Error())
			http.Error(w, `{"error":"delivery unavailable, retry shortly"}`, http.StatusServiceUnavailable)
			return
		}
		auditStepUp("challenge_sent", req.PatientID, "")
		writeJSON(w, http.StatusOK, map[string]string{"status": "challenge_sent"})
	})

	mux.HandleFunc("POST /portal/stepup/verify", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
			Phone     string `json:"phone"`
			Code      string `json:"code"`
		}
		if !decode(w, r, &req) {
			return
		}
		sub, err := s.svc.store.Subscription(req.PatientID)
		if err != nil || sub.Phone != NormalizePhone(req.Phone) || req.PatientID == "" {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		tok, ok := s.svc.stepup.verify(req.PatientID, req.Code)
		if !ok {
			auditStepUp("verify_failed", req.PatientID, "")
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		auditStepUp("elevated", req.PatientID, "")
		writeJSON(w, http.StatusOK, map[string]string{
			"status": "elevated", "stepup_token": tok, "expires_in": "600",
		})
	})
}

// requireElevation gates a PHI handler: subscription pair must match AND a
// valid step-up token must survive the random re-auth roll. 428 tells the
// app "render the challenge screen", never "you are wrong about identity".
func (s server) requireElevation(w http.ResponseWriter, r *http.Request) (patientID string, ok bool) {
	patientID = r.URL.Query().Get("patient_id")
	phone := NormalizePhone(r.URL.Query().Get("phone"))
	sub, err := s.svc.store.Subscription(patientID)
	if err != nil || sub.Phone != phone || patientID == "" || phone == "" {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return "", false
	}
	tok := r.Header.Get("X-StepUp-Token")
	elev, why := s.svc.stepup.elevated(patientID, tok)
	if !elev {
		auditStepUp("blocked_"+why, patientID, r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(428) // Precondition Required
		json.NewEncoder(w).Encode(map[string]string{"error": stepUpChallenge})
		return "", false
	}
	auditStepUp("phi_read", patientID, r.URL.Path)
	return patientID, true
}
