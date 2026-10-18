// Package authz — OIDC bearer-token verification for OpenHealth OS
// gateways, stdlib-only by design (ADR-0005: security-critical paths carry
// no third-party dependencies we cannot audit line by line).
//
// Verifier fetches the realm JWKS from a Keycloak-compatible issuer and
// verifies RS256 JWTs: signature, expiry, issuer, audience. Middleware
// rejects unattributed requests (401) and injects verified claims into the
// request context for downstream attribution.
//
// Wiring is opt-in per service via environment: OIDC_ISSUER set → enforced;
// unset → open (edge dev mode). Enforcement is all-or-nothing per process —
// a half-authenticated gateway is worse than a clearly-open dev box.
package authz

import (
	"context"
	"crypto"
	"crypto/rsa"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"time"
)

// Claims is the verified token claim set.
type Claims struct {
	Subject   string `json:"sub"`
	Issuer    string `json:"iss"`
	Audience  string `json:"aud"`
	ExpiresAt int64  `json:"exp"`
	Facility  string `json:"facility_id,omitempty"` // realm-mapped facility attribution
}

var (
	ErrNoToken      = errors.New("missing bearer token")
	ErrBadToken     = errors.New("malformed token")
	ErrBadSignature = errors.New("signature verification failed")
	ErrExpired      = errors.New("token expired")
	ErrIssuer       = errors.New("issuer mismatch")
	ErrAudience     = errors.New("audience mismatch")
	ErrUnknownKey   = errors.New("no JWKS key for token kid")
	ErrBadAlgorithm = errors.New("only RS256 tokens are accepted")
)

// jwk is the JWKS key shape (RSA public keys only).
type jwk struct {
	Kty string `json:"kty"`
	Kid string `json:"kid"`
	Alg string `json:"alg"`
	Use string `json:"use"`
	N   string `json:"n"` // base64url modulus
	E   string `json:"e"` // base64url exponent
}

type jwks struct {
	Keys []jwk `json:"keys"`
}

// Verifier verifies RS256 JWTs against an issuer's JWKS.
type Verifier struct {
	Issuer   string // expected iss, e.g. https://keycloak:8080/realms/openhealth
	Audience string // expected aud; empty skips the audience check
	JWKSURL  string // default: Issuer + "/protocol/openid-connect/certs"

	mu      sync.RWMutex
	keys    map[string]*rsa.PublicKey
	fetched time.Time
	ttl     time.Duration
	now     func() time.Time
	httpGet func(url string) (*http.Response, error) // test seam
}

// NewVerifier builds a verifier for an issuer. Audience may be empty.
func NewVerifier(issuer, audience string) *Verifier {
	return &Verifier{
		Issuer:   issuer,
		Audience: audience,
		JWKSURL:  strings.TrimSuffix(issuer, "/") + "/protocol/openid-connect/certs",
		keys:     map[string]*rsa.PublicKey{},
		ttl:      5 * time.Minute,
		now:      func() time.Time { return time.Now().UTC() },
		httpGet:  http.Get,
	}
}

func decodeSegment(seg string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(seg)
}

func (v *Verifier) refreshKeys() error {
	v.mu.RLock()
	fresh := v.now().Sub(v.fetched) < v.ttl && len(v.keys) > 0
	v.mu.RUnlock()
	if fresh {
		return nil
	}
	resp, err := v.httpGet(v.JWKSURL)
	if err != nil {
		return fmt.Errorf("jwks fetch: %w", err)
	}
	defer resp.Body.Close()
	var set jwks
	if err := json.NewDecoder(resp.Body).Decode(&set); err != nil {
		return fmt.Errorf("jwks decode: %w", err)
	}
	keys := map[string]*rsa.PublicKey{}
	for _, k := range set.Keys {
		if k.Kty != "RSA" {
			continue
		}
		nBytes, err := decodeSegment(k.N)
		if err != nil {
			continue
		}
		eBytes, err := decodeSegment(k.E)
		if err != nil {
			continue
		}
		e := 0
		for _, b := range eBytes {
			e = e<<8 | int(b)
		}
		keys[k.Kid] = &rsa.PublicKey{N: new(big.Int).SetBytes(nBytes), E: e}
	}
	v.mu.Lock()
	v.keys = keys
	v.fetched = v.now()
	v.mu.Unlock()
	return nil
}

func (v *Verifier) keyFor(kid string) (*rsa.PublicKey, error) {
	if err := v.refreshKeys(); err != nil {
		return nil, err
	}
	v.mu.RLock()
	key, ok := v.keys[kid]
	v.mu.RUnlock()
	if !ok {
		// One forced refresh — key rotation should not reject valid tokens.
		v.mu.Lock()
		v.fetched = time.Time{}
		v.mu.Unlock()
		if err := v.refreshKeys(); err != nil {
			return nil, err
		}
		v.mu.RLock()
		key, ok = v.keys[kid]
		v.mu.RUnlock()
		if !ok {
			return nil, ErrUnknownKey
		}
	}
	return key, nil
}

// Verify validates a compact JWT and returns its claims.
func (v *Verifier) Verify(token string) (Claims, error) {
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return Claims{}, ErrBadToken
	}
	headerJSON, err := decodeSegment(parts[0])
	if err != nil {
		return Claims{}, ErrBadToken
	}
	var header struct {
		Alg string `json:"alg"`
		Kid string `json:"kid"`
	}
	if err := json.Unmarshal(headerJSON, &header); err != nil {
		return Claims{}, ErrBadToken
	}
	if header.Alg != "RS256" {
		return Claims{}, ErrBadAlgorithm
	}
	key, err := v.keyFor(header.Kid)
	if err != nil {
		return Claims{}, err
	}
	payloadJSON, err := decodeSegment(parts[1])
	if err != nil {
		return Claims{}, ErrBadToken
	}
	sig, err := decodeSegment(parts[2])
	if err != nil {
		return Claims{}, ErrBadToken
	}
	digest := sha256.Sum256([]byte(parts[0] + "." + parts[1]))
	if err := rsa.VerifyPKCS1v15(key, crypto.SHA256, digest[:], sig); err != nil {
		return Claims{}, ErrBadSignature
	}
	var claims Claims
	if err := json.Unmarshal(payloadJSON, &claims); err != nil {
		return Claims{}, ErrBadToken
	}
	if claims.ExpiresAt != 0 && v.now().Unix() > claims.ExpiresAt {
		return Claims{}, ErrExpired
	}
	if v.Issuer != "" && claims.Issuer != v.Issuer {
		return Claims{}, ErrIssuer
	}
	if v.Audience != "" && claims.Audience != v.Audience {
		return Claims{}, ErrAudience
	}
	return claims, nil
}

type claimsKey struct{}

// FromContext returns the verified claims injected by Middleware.
func FromContext(ctx context.Context) (Claims, bool) {
	c, ok := ctx.Value(claimsKey{}).(Claims)
	return c, ok
}

// Middleware enforces bearer auth on every request except /healthz and
// /metrics — probes and scrapes are infrastructure, not data access.
// NetworkPolicy (infra/k8s) restricts who can reach them.
func (v *Verifier) Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" || r.URL.Path == "/metrics" {
			next.ServeHTTP(w, r)
			return
		}
		token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if token == "" || token == r.Header.Get("Authorization") {
			http.Error(w, ErrNoToken.Error(), http.StatusUnauthorized)
			return
		}
		claims, err := v.Verify(token)
		if err != nil {
			http.Error(w, err.Error(), http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), claimsKey{}, claims)))
	})
}

// FromEnv builds middleware from OIDC_ISSUER / OIDC_AUDIENCE. Returns nil
// when unset — callers leave the mux unwrapped (edge dev mode).
func FromEnv(getenv func(string) string) *Verifier {
	issuer := getenv("OIDC_ISSUER")
	if issuer == "" {
		return nil
	}
	return NewVerifier(issuer, getenv("OIDC_AUDIENCE"))
}

// RequireFromEnv is FromEnv with a production posture: when AUTH_REQUIRED=1
// the service MUST run behind OIDC — a missing issuer is a boot failure,
// never a silent downgrade to open access (ADR-0013). State-plane
// manifests set AUTH_REQUIRED=1; edge and dev stay permissive by default.
func RequireFromEnv(getenv func(string) string) (*Verifier, error) {
	v := FromEnv(getenv)
	if v == nil && getenv("AUTH_REQUIRED") == "1" {
		return nil, errors.New("AUTH_REQUIRED=1 but OIDC_ISSUER is unset — refusing to start unauthenticated")
	}
	return v, nil
}
