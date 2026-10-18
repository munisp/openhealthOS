package main

// Passkey unlock for the patient portal (ADR-0017): a WebAuthn/FIDO2
// credential bound to the patient's subscription, so returning users
// unlock the app with the phone's own biometric screen instead of
// re-typing id + phone number. Verification lives in shared/webauthn
// (stdlib ES256); this file is the transport + storage glue.
//
// Trust anchor: registration is gated by the same subscription + phone
// check as /portal/record — you can only bind a passkey to a session
// you already authenticated. Challenges are single-use with a 5-minute
// TTL and live in memory (ephemeral by nature — a restarted service
// just re-issues them).

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/munisp/openhealthOS/shared/webauthn"
)

// PasskeyCredential is the stored form (Store seam persists it).
type PasskeyCredential struct {
	CredID    string `json:"cred_id"` // base64url credential id
	PatientID string `json:"patient_id"`
	PubX      string `json:"pub_x"`
	PubY      string `json:"pub_y"`
	SignCount uint32 `json:"sign_count"`
	CreatedAt string `json:"created_at"`
}

func toStored(c *webauthn.Credential, patientID string) PasskeyCredential {
	return PasskeyCredential{
		CredID: c.ID, PatientID: patientID,
		PubX: c.PubX, PubY: c.PubY, SignCount: c.SignCount,
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
	}
}

func (p PasskeyCredential) toWebauthn() *webauthn.Credential {
	return &webauthn.Credential{ID: p.CredID, PubX: p.PubX, PubY: p.PubY, SignCount: p.SignCount}
}

// ---------------- challenge jar (ephemeral, single-use, TTL'd) ----------------

type challengeJar struct {
	mu   sync.Mutex
	byID map[string]jarEntry
}

type jarEntry struct {
	challenge string
	patientID string
	expires   time.Time
}

func newChallengeJar() *challengeJar { return &challengeJar{byID: map[string]jarEntry{}} }

const challengeTTL = 5 * time.Minute

func (j *challengeJar) put(patientID, challenge string) {
	j.mu.Lock()
	defer j.mu.Unlock()
	// sweep expired on write — bounded jar without a background loop
	for k, e := range j.byID {
		if time.Now().After(e.expires) {
			delete(j.byID, k)
		}
	}
	j.byID[patientID] = jarEntry{challenge: challenge, patientID: patientID, expires: time.Now().Add(challengeTTL)}
}

// take consumes the challenge — single-use, so a replayed ceremony
// response dies here even before signature checks.
func (j *challengeJar) take(patientID string) (string, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	e, ok := j.byID[patientID]
	if !ok || time.Now().After(e.expires) {
		return "", errors.New("no live challenge — begin again")
	}
	delete(j.byID, patientID)
	return e.challenge, nil
}

// ---------------- config ----------------

type passkeyConfig struct {
	RPID    string
	Origins []string
}

// passkeyConfigFromEnv: PORTAL_RP_ID is the relying-party id (the portal
// host, e.g. "portal.openhealth.ng" — no scheme); PORTAL_ORIGINS is the
// comma-separated allowlist of origins the browser may assert.
// Defaults are the dev gateway.
func passkeyConfigFromEnv(getenv func(string) string) passkeyConfig {
	rpID := getenv("PORTAL_RP_ID")
	if rpID == "" {
		rpID = "localhost"
	}
	origins := strings.Split(getenv("PORTAL_ORIGINS"), ",")
	out := []string{}
	for _, o := range origins {
		if o = strings.TrimSpace(o); o != "" {
			out = append(out, o)
		}
	}
	if len(out) == 0 {
		out = []string{"http://localhost", "http://localhost:8080", "http://127.0.0.1:8080"}
	}
	return passkeyConfig{RPID: rpID, Origins: out}
}

// ---------------- passkey store seam ----------------

// PasskeyStore is satisfied by the service Store (memory + Postgres).
type PasskeyStore interface {
	PutPasskey(p PasskeyCredential) error
	Passkeys(patientID string) []PasskeyCredential
	PasskeyByCredID(credID string) (PasskeyCredential, error)
	UpdatePasskeySignCount(credID string, count uint32) error
}

// ---------------- http ----------------

type passkeyAPI struct {
	Store PasskeyStore
	Subs  interface {
		Subscription(patientID string) (Subscription, error)
	}
	Jar    *challengeJar
	Config passkeyConfig
}

func b64d(s string) ([]byte, error) {
	if b, err := base64.RawURLEncoding.DecodeString(s); err == nil {
		return b, nil
	}
	return base64.URLEncoding.DecodeString(s)
}

func (a passkeyAPI) registerRoutes(mux *http.ServeMux) {
	// begin: gated like /portal/record — the phone on file is the trust
	// anchor for binding a new credential.
	mux.HandleFunc("POST /portal/passkey/register/begin", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
			Phone     string `json:"phone"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.PatientID == "" || req.Phone == "" {
			http.Error(w, `{"error":"patient_id and phone required"}`, http.StatusBadRequest)
			return
		}
		sub, err := a.Subs.Subscription(req.PatientID)
		if err != nil || NormalizePhone(sub.Phone) != NormalizePhone(req.Phone) {
			// indistinguishable 404 — never confirm an id exists
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		ch := webauthn.NewChallenge()
		a.Jar.put(req.PatientID, ch)
		writeJSON(w, 200, map[string]any{
			"challenge": ch,
			"rp_id":     a.Config.RPID,
			"user_id":   base64.RawURLEncoding.EncodeToString([]byte(req.PatientID)),
		})
	})

	mux.HandleFunc("POST /portal/passkey/register/finish", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID         string `json:"patient_id"`
			AttestationObject string `json:"attestation_object"` // base64url
			ClientDataJSON    string `json:"client_data_json"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.PatientID == "" ||
			req.AttestationObject == "" || req.ClientDataJSON == "" {
			http.Error(w, `{"error":"patient_id, attestation_object and client_data_json required"}`, http.StatusBadRequest)
			return
		}
		ch, err := a.Jar.take(req.PatientID)
		if err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
			return
		}
		attObj, err1 := b64d(req.AttestationObject)
		cd, err2 := b64d(req.ClientDataJSON)
		if err1 != nil || err2 != nil {
			http.Error(w, `{"error":"bad base64"}`, http.StatusBadRequest)
			return
		}
		cred, err := webauthn.ParseRegistration(attObj, cd, ch, a.Config.Origins, a.Config.RPID)
		if err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
			return
		}
		if err := a.Store.PutPasskey(toStored(cred, req.PatientID)); err != nil {
			http.Error(w, `{"error":"store failed"}`, http.StatusInternalServerError)
			return
		}
		writeJSON(w, http.StatusCreated, map[string]any{"registered": true, "cred_id": cred.ID})
	})

	mux.HandleFunc("POST /portal/passkey/login/begin", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.PatientID == "" {
			http.Error(w, `{"error":"patient_id required"}`, http.StatusBadRequest)
			return
		}
		creds := a.Store.Passkeys(req.PatientID)
		if len(creds) == 0 {
			http.Error(w, `{"error":"no passkey enrolled"}`, http.StatusNotFound)
			return
		}
		allow := []string{}
		for _, c := range creds {
			allow = append(allow, c.CredID)
		}
		ch := webauthn.NewChallenge()
		a.Jar.put(req.PatientID, ch)
		writeJSON(w, 200, map[string]any{
			"challenge":         ch,
			"rp_id":             a.Config.RPID,
			"allow_credentials": allow,
		})
	})

	mux.HandleFunc("POST /portal/passkey/login/finish", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID         string `json:"patient_id"`
			CredID            string `json:"cred_id"`
			AuthenticatorData string `json:"authenticator_data"`
			ClientDataJSON    string `json:"client_data_json"`
			Signature         string `json:"signature"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.PatientID == "" ||
			req.CredID == "" || req.AuthenticatorData == "" || req.ClientDataJSON == "" || req.Signature == "" {
			http.Error(w, `{"error":"patient_id, cred_id, authenticator_data, client_data_json and signature required"}`, http.StatusBadRequest)
			return
		}
		ch, err := a.Jar.take(req.PatientID)
		if err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
			return
		}
		stored, err := a.Store.PasskeyByCredID(req.CredID)
		if err != nil || stored.PatientID != req.PatientID {
			http.Error(w, `{"error":"unknown credential"}`, http.StatusNotFound)
			return
		}
		authRaw, e1 := b64d(req.AuthenticatorData)
		cd, e2 := b64d(req.ClientDataJSON)
		sig, e3 := b64d(req.Signature)
		if e1 != nil || e2 != nil || e3 != nil {
			http.Error(w, `{"error":"bad base64"}`, http.StatusBadRequest)
			return
		}
		count, err := webauthn.VerifyAssertion(stored.toWebauthn(), authRaw, cd, sig, ch, a.Config.Origins, a.Config.RPID)
		if err != nil {
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusUnauthorized)
			return
		}
		if err := a.Store.UpdatePasskeySignCount(req.CredID, count); err != nil {
			http.Error(w, `{"error":"counter persist failed"}`, http.StatusInternalServerError)
			return
		}
		writeJSON(w, 200, map[string]any{"ok": true, "patient_id": req.PatientID})
	})
}
