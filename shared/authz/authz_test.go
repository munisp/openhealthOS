package authz

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math/big"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// testKeys builds an RSA keypair and a JWKS document for it.
type testKeys struct {
	priv *rsa.PrivateKey
	jwks string
	kid  string
}

func newTestKeys(t *testing.T) testKeys {
	t.Helper()
	priv, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatalf("keygen: %v", err)
	}
	kidBytes := make([]byte, 6)
	rand.Read(kidBytes)
	kid := "test-key-" + base64.RawURLEncoding.EncodeToString(kidBytes)
	jwksDoc := map[string]any{"keys": []map[string]any{{
		"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
		"n": base64.RawURLEncoding.EncodeToString(priv.PublicKey.N.Bytes()),
		"e": base64.RawURLEncoding.EncodeToString(big.NewInt(int64(priv.PublicKey.E)).Bytes()),
	}}}
	raw, _ := json.Marshal(jwksDoc)
	return testKeys{priv: priv, jwks: string(raw), kid: kid}
}

// sign mints a compact RS256 JWT over the given claims.
func (k testKeys) sign(t *testing.T, claims map[string]any) string {
	t.Helper()
	header, _ := json.Marshal(map[string]string{"alg": "RS256", "kid": k.kid, "typ": "JWT"})
	payload, _ := json.Marshal(claims)
	h := base64.RawURLEncoding.EncodeToString(header)
	p := base64.RawURLEncoding.EncodeToString(payload)
	digest := sha256.Sum256([]byte(h + "." + p))
	sig, err := rsa.SignPKCS1v15(rand.Reader, k.priv, crypto.SHA256, digest[:])
	if err != nil {
		t.Fatalf("sign: %v", err)
	}
	return h + "." + p + "." + base64.RawURLEncoding.EncodeToString(sig)
}

func newVerifier(keys testKeys) *Verifier {
	v := NewVerifier("https://idp.example/realms/openhealth", "openhealth")
	v.JWKSURL = "https://idp.example/certs"
	v.now = func() time.Time { return time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC) }
	v.httpGet = func(url string) (*http.Response, error) {
		if url != "https://idp.example/certs" {
			return nil, errors.New("unexpected JWKS URL: " + url)
		}
		return &http.Response{
			StatusCode: 200,
			Body:       io.NopCloser(strings.NewReader(keys.jwks)),
		}, nil
	}
	return v
}

func goodClaims() map[string]any {
	return map[string]any{
		"sub": "nurse-7", "iss": "https://idp.example/realms/openhealth",
		"aud": "openhealth", "exp": time.Date(2026, 10, 18, 13, 0, 0, 0, time.UTC).Unix(),
		"facility_id": "NG-KD-0014",
	}
}

func TestVerifyGoodToken(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	claims, err := v.Verify(keys.sign(t, goodClaims()))
	if err != nil {
		t.Fatalf("verify: %v", err)
	}
	if claims.Subject != "nurse-7" || claims.Facility != "NG-KD-0014" {
		t.Fatalf("claims: %+v", claims)
	}
}

func TestRejectTamperedSignature(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	token := keys.sign(t, goodClaims())
	parts := strings.Split(token, ".")
	// Forge a claim, keep the old signature.
	forged, _ := json.Marshal(map[string]any{"sub": "attacker", "exp": parts[1]})
	parts[1] = base64.RawURLEncoding.EncodeToString(forged)
	_, err := v.Verify(strings.Join(parts, "."))
	if !errors.Is(err, ErrBadSignature) && !errors.Is(err, ErrIssuer) && !errors.Is(err, ErrExpired) {
		t.Fatalf("tampered token accepted: %v", err)
	}
}

func TestRejectExpired(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	claims := goodClaims()
	claims["exp"] = time.Date(2026, 10, 18, 11, 0, 0, 0, time.UTC).Unix()
	if _, err := v.Verify(keys.sign(t, claims)); !errors.Is(err, ErrExpired) {
		t.Fatalf("want ErrExpired, got %v", err)
	}
}

func TestRejectWrongIssuerAndAudience(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	badIss := goodClaims()
	badIss["iss"] = "https://evil.example"
	if _, err := v.Verify(keys.sign(t, badIss)); !errors.Is(err, ErrIssuer) {
		t.Fatalf("want ErrIssuer, got %v", err)
	}
	badAud := goodClaims()
	badAud["aud"] = "other-service"
	if _, err := v.Verify(keys.sign(t, badAud)); !errors.Is(err, ErrAudience) {
		t.Fatalf("want ErrAudience, got %v", err)
	}
}

func TestRejectUnknownKid(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	other := newTestKeys(t) // different keypair + kid
	_, err := v.Verify(other.sign(t, goodClaims()))
	if !errors.Is(err, ErrUnknownKey) {
		t.Fatalf("want ErrUnknownKey, got %v", err)
	}
}

func TestRejectNonRS256(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	header, _ := json.Marshal(map[string]string{"alg": "HS256", "kid": "x"})
	payload, _ := json.Marshal(goodClaims())
	token := base64.RawURLEncoding.EncodeToString(header) + "." +
		base64.RawURLEncoding.EncodeToString(payload) + ".sig"
	if _, err := v.Verify(token); !errors.Is(err, ErrBadAlgorithm) {
		t.Fatalf("want ErrBadAlgorithm, got %v", err)
	}
}

func TestKeyRotationRefresh(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	current := keys.jwks
	v.httpGet = func(url string) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(current))}, nil
	}
	// Prime the cache with the old keyset.
	if _, err := v.Verify(keys.sign(t, goodClaims())); err != nil {
		t.Fatalf("prime: %v", err)
	}
	// Rotate: new keypair becomes the only JWKS key.
	rotated := newTestKeys(t)
	current = rotated.jwks
	if _, err := v.Verify(rotated.sign(t, goodClaims())); err != nil {
		t.Fatalf("rotated key rejected after forced refresh: %v", err)
	}
}

func TestMiddleware(t *testing.T) {
	keys := newTestKeys(t)
	v := newVerifier(keys)
	protected := v.Middleware(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/healthz" { // probes bypass auth, hence no claims
			claims, ok := FromContext(r.Context())
			if !ok || claims.Subject != "nurse-7" {
				t.Error("claims not in context")
			}
		}
		w.Write([]byte("ok"))
	}))

	// No token → 401.
	rec := httptest.NewRecorder()
	protected.ServeHTTP(rec, httptest.NewRequest("GET", "/fhir/Observation/x", nil))
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated: want 401, got %d", rec.Code)
	}
	// Good token → 200 with claims in context.
	req := httptest.NewRequest("GET", "/fhir/Observation/x", nil)
	req.Header.Set("Authorization", "Bearer "+keys.sign(t, goodClaims()))
	rec = httptest.NewRecorder()
	protected.ServeHTTP(rec, req)
	if rec.Code != 200 {
		t.Fatalf("authenticated: want 200, got %d (%s)", rec.Code, rec.Body)
	}
	// /healthz stays open for probes.
	rec = httptest.NewRecorder()
	protected.ServeHTTP(rec, httptest.NewRequest("GET", "/healthz", nil))
	if rec.Code != 200 {
		t.Fatalf("healthz must bypass auth: got %d", rec.Code)
	}
}

func TestFromEnv(t *testing.T) {
	if v := FromEnv(func(string) string { return "" }); v != nil {
		t.Fatal("empty issuer must yield nil verifier (dev mode)")
	}
	v := FromEnv(func(k string) string {
		if k == "OIDC_ISSUER" {
			return "https://idp.example/realms/openhealth"
		}
		return ""
	})
	if v == nil || v.JWKSURL != "https://idp.example/realms/openhealth/protocol/openid-connect/certs" {
		t.Fatalf("FromEnv: %+v", v)
	}
}
