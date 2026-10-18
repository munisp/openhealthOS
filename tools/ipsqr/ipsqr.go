// OpenHealth OS — offline-verifiable IPS QR payloads (ADR-0013).
//
// A signed IPS summary fits in a QR: "OHIPS1.<payload>.<signature>" where
// payload is base64url(canonical JSON) and signature is base64url(ed25519
// over SHA-256(payload)). Verification needs only the issuer's public key —
// no network — so a receiving facility can trust a referral summary on a
// printed card in a dead zone.
package main

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

const Prefix = "OHIPS1"

var (
	ErrBadPrefix = errors.New("not an OHIPS1 payload")
	ErrBadShape  = errors.New("payload must have exactly three segments")
	ErrBadSig    = errors.New("signature verification failed")
	ErrKeyLength = errors.New("ed25519 keys must be 32-byte seed or 64-byte private key")
)

func b64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func unb64(s string) ([]byte, error) { return base64.RawURLEncoding.DecodeString(s) }

// CanonicalJSON marshals with sorted object keys (encoding/json orders map
// keys), so signer and verifier byte-match regardless of struct ordering.
func CanonicalJSON(v any) ([]byte, error) { return json.Marshal(v) }

func GenerateKey() (ed25519.PublicKey, ed25519.PrivateKey, error) {
	return ed25519.GenerateKey(rand.Reader)
}

// PrivateFromSeed accepts a 32-byte seed or a full 64-byte private key.
func PrivateFromSeed(raw []byte) (ed25519.PrivateKey, error) {
	switch len(raw) {
	case ed25519.SeedSize:
		return ed25519.NewKeyFromSeed(raw), nil
	case ed25519.PrivateKeySize:
		return ed25519.PrivateKey(raw), nil
	default:
		return nil, ErrKeyLength
	}
}

func SignPayload(payload []byte, priv ed25519.PrivateKey) string {
	digest := sha256.Sum256(payload)
	sig := ed25519.Sign(priv, digest[:])
	return Prefix + "." + b64(payload) + "." + b64(sig)
}

func VerifyPayload(qr string, pub ed25519.PublicKey) ([]byte, error) {
	parts := strings.Split(qr, ".")
	if len(parts) != 3 {
		return nil, ErrBadShape
	}
	if parts[0] != Prefix {
		return nil, ErrBadPrefix
	}
	payload, err := unb64(parts[1])
	if err != nil {
		return nil, fmt.Errorf("payload decode: %w", err)
	}
	sig, err := unb64(parts[2])
	if err != nil {
		return nil, fmt.Errorf("signature decode: %w", err)
	}
	digest := sha256.Sum256(payload)
	if !ed25519.Verify(pub, digest[:], sig) {
		return nil, ErrBadSig
	}
	return payload, nil
}
