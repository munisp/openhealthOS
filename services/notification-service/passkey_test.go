package main

// Passkey portal endpoints (ADR-0017): full register + login ceremonies
// over HTTP with a synthetic authenticator — real P-256 keys, real
// signatures, the shared/webauthn verifier doing the checks. The gate
// (subscription + phone) and the single-use challenge jar are covered
// too.

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

const (
	pkRPID   = "localhost"
	pkOrigin = "http://localhost:8080"
)

func pkConfig() passkeyConfig {
	return passkeyConfig{RPID: pkRPID, Origins: []string{pkOrigin}}
}

// --- synthetic authenticator helpers (CBOR encoders mirror webauthn tests) ---

func cbHead(major byte, arg uint64) []byte {
	m := major << 5
	switch {
	case arg < 24:
		return []byte{m | byte(arg)}
	case arg < 256:
		return []byte{m | 24, byte(arg)}
	default:
		return []byte{m | 25, byte(arg >> 8), byte(arg)}
	}
}

func cbBytes(b []byte) []byte { return append(cbHead(2, uint64(len(b))), b...) }
func cbText(s string) []byte  { return append(cbHead(3, uint64(len(s))), s...) }
func cbUint(v uint64) []byte  { return cbHead(0, v) }
func cbNint(v int64) []byte   { return cbHead(1, uint64(-1-v)) }
func cbMap(pairs ...[]byte) []byte {
	out := cbHead(5, uint64(len(pairs)/2))
	for _, p := range pairs {
		out = append(out, p...)
	}
	return out
}

func pad32(b []byte) []byte {
	if len(b) >= 32 {
		return b
	}
	out := make([]byte, 32)
	copy(out[32-len(b):], b)
	return out
}

func pkCoseKey(priv *ecdsa.PrivateKey) []byte {
	return cbMap(
		cbUint(1), cbUint(2),
		cbUint(3), cbNint(-7),
		cbNint(-1), cbUint(1),
		cbNint(-2), cbBytes(pad32(priv.PublicKey.X.Bytes())),
		cbNint(-3), cbBytes(pad32(priv.PublicKey.Y.Bytes())),
	)
}

func pkRPHash() []byte { s := sha256.Sum256([]byte(pkRPID)); return s[:] }

func pkRegAuthData(credID []byte, priv *ecdsa.PrivateKey) []byte {
	out := append([]byte{}, pkRPHash()...)
	out = append(out, 0x01|0x40)
	out = binary.BigEndian.AppendUint32(out, 0)
	out = append(out, make([]byte, 16)...)
	out = binary.BigEndian.AppendUint16(out, uint16(len(credID)))
	out = append(out, credID...)
	out = append(out, pkCoseKey(priv)...)
	return out
}

func pkAssertAuthData(count uint32) []byte {
	out := append([]byte{}, pkRPHash()...)
	out = append(out, 0x01|0x04)
	return binary.BigEndian.AppendUint32(out, count)
}

func pkClientData(typ, challenge string) []byte {
	raw, _ := json.Marshal(map[string]string{"type": typ, "challenge": challenge, "origin": pkOrigin})
	return raw
}

func b64u(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func pkSign(t *testing.T, priv *ecdsa.PrivateKey, authData, cd []byte) []byte {
	t.Helper()
	sum := sha256.Sum256(cd)
	digest := sha256.Sum256(append(append([]byte{}, authData...), sum[:]...))
	sig, err := ecdsa.SignASN1(rand.Reader, priv, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return sig
}

// --- test rig ---

func pkRig(t *testing.T) (*MemoryStore, http.Handler) {
	t.Helper()
	store := NewMemoryStore()
	if err := store.PutSubscription(Subscription{
		PatientID: "P-1", Phone: "08031234567", Language: "en", Facility: "F-1",
	}); err != nil {
		t.Fatal(err)
	}
	api := passkeyAPI{Store: store, Subs: store, Jar: newChallengeJar(), Config: pkConfig()}
	mux := http.NewServeMux()
	api.registerRoutes(mux)
	return store, mux
}

func pkPost(t *testing.T, h http.Handler, path string, body map[string]any) (int, map[string]any) {
	t.Helper()
	raw, _ := json.Marshal(body)
	req := httptest.NewRequest(http.MethodPost, path, bytes.NewReader(raw))
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	var out map[string]any
	json.NewDecoder(rr.Body).Decode(&out)
	return rr.Code, out
}

func TestPasskeyCeremonyEndToEnd(t *testing.T) {
	store, h := pkRig(t)
	priv, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	credID := []byte("pk-cred-0001")

	// register/begin — phone must match the subscription.
	code, out := pkPost(t, h, "/portal/passkey/register/begin", map[string]any{
		"patient_id": "P-1", "phone": "08031234567"})
	if code != 200 || out["challenge"] == "" || out["rp_id"] != pkRPID {
		t.Fatalf("begin: %d %v", code, out)
	}
	challenge := out["challenge"].(string)

	// register/finish with the synthetic attestation.
	attObj := cbMap(
		cbText("fmt"), cbText("none"),
		cbText("attStmt"), cbMap(),
		cbText("authData"), cbBytes(pkRegAuthData(credID, priv)),
	)
	code, out = pkPost(t, h, "/portal/passkey/register/finish", map[string]any{
		"patient_id": "P-1", "attestation_object": b64u(attObj),
		"client_data_json": b64u(pkClientData("webauthn.create", challenge))})
	if code != http.StatusCreated {
		t.Fatalf("finish: %d %v", code, out)
	}
	if got := len(store.Passkeys("P-1")); got != 1 {
		t.Fatalf("stored passkeys: %d", got)
	}

	// login/begin advertises the credential.
	code, out = pkPost(t, h, "/portal/passkey/login/begin", map[string]any{"patient_id": "P-1"})
	if code != 200 {
		t.Fatalf("login begin: %d %v", code, out)
	}
	challenge = out["challenge"].(string)

	// login/finish — real signature, advancing counter.
	authData := pkAssertAuthData(3)
	cd := pkClientData("webauthn.get", challenge)
	code, out = pkPost(t, h, "/portal/passkey/login/finish", map[string]any{
		"patient_id": "P-1", "cred_id": b64u(credID),
		"authenticator_data": b64u(authData), "client_data_json": b64u(cd),
		"signature": b64u(pkSign(t, priv, authData, cd))})
	if code != 200 || out["ok"] != true {
		t.Fatalf("login finish: %d %v", code, out)
	}
	k, _ := store.PasskeyByCredID(b64u(credID))
	if k.SignCount != 3 {
		t.Fatalf("sign count persisted: %d", k.SignCount)
	}
}

func TestPasskeyGatesAndReplay(t *testing.T) {
	_, h := pkRig(t)

	// Wrong phone → indistinguishable 404.
	code, _ := pkPost(t, h, "/portal/passkey/register/begin", map[string]any{
		"patient_id": "P-1", "phone": "08099999999"})
	if code != http.StatusNotFound {
		t.Fatalf("wrong phone must be 404, got %d", code)
	}
	// Unknown patient → same 404.
	code, _ = pkPost(t, h, "/portal/passkey/register/begin", map[string]any{
		"patient_id": "P-X", "phone": "08031234567"})
	if code != http.StatusNotFound {
		t.Fatalf("unknown patient must be 404, got %d", code)
	}
	// finish without begin → no live challenge.
	code, out := pkPost(t, h, "/portal/passkey/register/finish", map[string]any{
		"patient_id": "P-1", "attestation_object": "AA", "client_data_json": "AA"})
	if code != http.StatusBadRequest {
		t.Fatalf("finish without begin: %d %v", code, out)
	}
	// Login for a patient with no passkey → 404.
	code, _ = pkPost(t, h, "/portal/passkey/login/begin", map[string]any{"patient_id": "P-1"})
	if code != http.StatusNotFound {
		t.Fatalf("no passkey: %d", code)
	}
}

func TestChallengeIsSingleUse(t *testing.T) {
	jar := newChallengeJar()
	jar.put("P-1", "c-1")
	if _, err := jar.take("P-1"); err != nil {
		t.Fatal(err)
	}
	if _, err := jar.take("P-1"); err == nil {
		t.Fatal("challenge must be single-use")
	}
}

func TestPasskeyConfigFromEnv(t *testing.T) {
	cfg := passkeyConfigFromEnv(func(string) string { return "" })
	if cfg.RPID != "localhost" || len(cfg.Origins) == 0 {
		t.Fatalf("defaults: %+v", cfg)
	}
	env := map[string]string{"PORTAL_RP_ID": "portal.openhealth.ng", "PORTAL_ORIGINS": "https://portal.openhealth.ng, https://app.openhealth.ng"}
	cfg = passkeyConfigFromEnv(func(k string) string { return env[k] })
	if cfg.RPID != "portal.openhealth.ng" || len(cfg.Origins) != 2 {
		t.Fatalf("env config: %+v", cfg)
	}
}
