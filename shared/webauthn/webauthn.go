// Package webauthn — passkey (WebAuthn/FIDO2) verification, stdlib-only
// (ADR-0017).
//
// Why: the patient portal signs in with a patient id + phone number. A
// passkey upgrades that to phishing-resistant, device-bound unlock —
// the phone's own biometric screen guards the credential, so "unlock
// with fingerprint" in the app is real cryptography, not a PIN stored
// in localStorage.
//
// Scope (honest): registration and assertion for ES256 (COSE alg -7,
// ECDSA P-256) passkeys with fmt="none" attestation — exactly what
// platform authenticators (Android Keystore, iOS Secure Enclave) emit
// for passkeys. Attestation *trust* (verifying the device vendor's
// certificate chain) is intentionally out of scope: the platform binds
// the key to the patient at a session already gated by the subscription
// phone check, which is the trust anchor — device provenance adds
// nothing against our fraud model (a shared phone is stopped by the
// registry-side biometric, not by attestation).
//
// No third-party CBOR/WebAuthn library is embedded (ADR-0005); the
// minimal CBOR subset below parses exactly what authenticators produce.
package webauthn

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
)

// Credential is a registered passkey: the authenticator's credential id
// and its public key, plus the signature counter for clone detection.
type Credential struct {
	ID        string           `json:"id"` // base64url of the raw credential id
	PublicKey *ecdsa.PublicKey `json:"-"`
	PubX      string           `json:"pub_x"` // big-endian base64url, 32 bytes
	PubY      string           `json:"pub_y"`
	SignCount uint32           `json:"sign_count"`
}

// KeyMaterial rehydrates the public key from the stored form.
func (c *Credential) KeyMaterial() (*ecdsa.PublicKey, error) {
	xb, err := base64.RawURLEncoding.DecodeString(c.PubX)
	if err != nil {
		return nil, err
	}
	yb, err := base64.RawURLEncoding.DecodeString(c.PubY)
	if err != nil {
		return nil, err
	}
	if len(xb) != 32 || len(yb) != 32 {
		return nil, errors.New("webauthn: bad key length")
	}
	return &ecdsa.PublicKey{
		Curve: elliptic.P256(),
		X:     new(big.Int).SetBytes(xb),
		Y:     new(big.Int).SetBytes(yb),
	}, nil
}

// NewChallenge mints a 256-bit base64url challenge.
func NewChallenge() string {
	var b [32]byte
	_, _ = rand.Read(b[:])
	return base64.RawURLEncoding.EncodeToString(b[:])
}

// ---------------------------------------------------------------- client data

type clientData struct {
	Type      string `json:"type"`
	Challenge string `json:"challenge"`
	Origin    string `json:"origin"`
}

func checkClientData(raw []byte, wantType, wantChallenge string, origins []string) error {
	var cd clientData
	if err := json.Unmarshal(raw, &cd); err != nil {
		return fmt.Errorf("webauthn: clientDataJSON: %w", err)
	}
	if cd.Type != wantType {
		return fmt.Errorf("webauthn: type %q, want %q", cd.Type, wantType)
	}
	if cd.Challenge != wantChallenge {
		return errors.New("webauthn: challenge mismatch")
	}
	for _, o := range origins {
		if cd.Origin == o {
			return nil
		}
	}
	return fmt.Errorf("webauthn: origin %q not allowed", cd.Origin)
}

// ---------------------------------------------------------------- auth data

// authData layout: rpIdHash(32) flags(1) signCount(4 BE) [attested data].
type authData struct {
	RPIDHash     []byte
	UserPresent  bool
	UserVerified bool
	SignCount    uint32
	CredID       []byte
	PubKey       *ecdsa.PublicKey // nil when no attested credential data
}

func parseAuthData(raw []byte, wantAttested bool) (authData, error) {
	var a authData
	if len(raw) < 37 {
		return a, errors.New("webauthn: authData too short")
	}
	a.RPIDHash = raw[:32]
	flags := raw[32]
	a.UserPresent = flags&0x01 != 0
	a.UserVerified = flags&0x04 != 0
	a.SignCount = binary.BigEndian.Uint32(raw[33:37])
	if !wantAttested {
		return a, nil
	}
	if flags&0x40 == 0 {
		return a, errors.New("webauthn: attested credential data absent")
	}
	rest := raw[37:]
	if len(rest) < 18 {
		return a, errors.New("webauthn: attested data truncated")
	}
	rest = rest[16:] // AAGUID
	credLen := int(binary.BigEndian.Uint16(rest[:2]))
	rest = rest[2:]
	if len(rest) < credLen {
		return a, errors.New("webauthn: credential id truncated")
	}
	a.CredID = rest[:credLen]
	key, err := parseCOSEKey(rest[credLen:])
	if err != nil {
		return a, err
	}
	a.PubKey = key
	return a, nil
}

func checkRPIDHash(a authData, rpID string) error {
	sum := sha256.Sum256([]byte(rpID))
	if !equalBytes(a.RPIDHash, sum[:]) {
		return errors.New("webauthn: rpIdHash mismatch")
	}
	return nil
}

func equalBytes(a, b []byte) bool {
	if len(a) != len(b) {
		return false
	}
	var v byte
	for i := range a {
		v |= a[i] ^ b[i]
	}
	return v == 0
}

// ---------------------------------------------------------------- registration

// ParseRegistration verifies a webauthn.create response and returns the
// credential to store. attestationObject and clientDataJSON are the raw
// ArrayBuffer bytes the browser produced.
func ParseRegistration(attestationObject, clientDataJSON []byte, challenge string, origins []string, rpID string) (*Credential, error) {
	if err := checkClientData(clientDataJSON, "webauthn.create", challenge, origins); err != nil {
		return nil, err
	}
	obj, err := cborParse(attestationObject)
	if err != nil {
		return nil, fmt.Errorf("webauthn: attestation object: %w", err)
	}
	m, ok := obj.(map[any]any)
	if !ok {
		return nil, errors.New("webauthn: attestation object not a map")
	}
	authRaw, ok := m["authData"].([]byte)
	if !ok {
		return nil, errors.New("webauthn: authData missing")
	}
	a, err := parseAuthData(authRaw, true)
	if err != nil {
		return nil, err
	}
	if !a.UserPresent {
		return nil, errors.New("webauthn: user presence flag unset")
	}
	if err := checkRPIDHash(a, rpID); err != nil {
		return nil, err
	}
	return &Credential{
		ID:        base64.RawURLEncoding.EncodeToString(a.CredID),
		PublicKey: a.PubKey,
		PubX:      base64.RawURLEncoding.EncodeToString(pad32(a.PubKey.X.Bytes())),
		PubY:      base64.RawURLEncoding.EncodeToString(pad32(a.PubKey.Y.Bytes())),
		SignCount: a.SignCount,
	}, nil
}

func pad32(b []byte) []byte {
	if len(b) >= 32 {
		return b
	}
	out := make([]byte, 32)
	copy(out[32-len(b):], b)
	return out
}

// ---------------------------------------------------------------- assertion

// VerifyAssertion checks a webauthn.get response against the stored
// credential and returns the new sign count. Clone detection: when both
// counters are live, a non-advancing counter means the credential was
// copied — rejected.
func VerifyAssertion(cred *Credential, authenticatorData, clientDataJSON, signature []byte, challenge string, origins []string, rpID string) (uint32, error) {
	if err := checkClientData(clientDataJSON, "webauthn.get", challenge, origins); err != nil {
		return 0, err
	}
	a, err := parseAuthData(authenticatorData, false)
	if err != nil {
		return 0, err
	}
	if !a.UserPresent {
		return 0, errors.New("webauthn: user presence flag unset")
	}
	if err := checkRPIDHash(a, rpID); err != nil {
		return 0, err
	}
	key := cred.PublicKey
	if key == nil {
		if key, err = cred.KeyMaterial(); err != nil {
			return 0, err
		}
	}
	sum := sha256.Sum256(clientDataJSON)
	digest := sha256.Sum256(append(append([]byte{}, authenticatorData...), sum[:]...))
	if !ecdsa.VerifyASN1(key, digest[:], signature) {
		return 0, errors.New("webauthn: bad signature")
	}
	if cred.SignCount > 0 && a.SignCount > 0 && a.SignCount <= cred.SignCount {
		return 0, errors.New("webauthn: sign count not advancing — cloned credential?")
	}
	return a.SignCount, nil
}
