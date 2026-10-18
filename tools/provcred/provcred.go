// OpenHealth OS — provider verifiable credentials (ADR-0013).
//
// A clinician's licence is a signed, revocable credential: council, licence
// number, scope, expiry. Verification is offline (issuer public key + a
// revocation list synced when the uplink exists), so a referral facility can
// trust "Dr. X is a licensed MDCN provider" without a live call. W3C-VC-ish
// shape, deliberately minimal: JSON + ed25519 proof, no LD bloat.
package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

var (
	ErrBadSig  = errors.New("credential proof verification failed")
	ErrExpired = errors.New("credential expired")
	ErrRevoked = errors.New("credential revoked")
	ErrNoProof = errors.New("credential has no proof")
)

type Provider struct {
	ProviderID string `json:"provider_id"`
	Name       string `json:"name"`
	Council    string `json:"council"` // MDCN | NMCN | PCN …
	LicenceNo  string `json:"licence_no"`
	Scope      string `json:"scope"` // physician | nurse | pharmacist | CHEW
	FacilityID string `json:"facility_id"`
}

type Credential struct {
	Context string   `json:"@context"` // "https://openhealthos.org/credentials/v1"
	Type    string   `json:"type"`     // "ProviderLicenceCredential"
	Issuer  string   `json:"issuer"`   // issuing authority id
	Issued  string   `json:"issued"`   // RFC3339
	Expiry  string   `json:"expiry"`   // RFC3339
	Subject Provider `json:"credentialSubject"`
	Proof   string   `json:"proof"` // base64url ed25519 over sha256(canonical cred sans proof)
}

func signingBytes(c Credential) ([]byte, error) {
	c.Proof = ""
	return json.Marshal(c) // struct field order is deterministic
}

func Issue(issuer string, subj Provider, priv ed25519.PrivateKey, ttl time.Duration) (Credential, error) {
	now := time.Now().UTC()
	c := Credential{
		Context: "https://openhealthos.org/credentials/v1",
		Type:    "ProviderLicenceCredential",
		Issuer:  issuer, Issued: now.Format(time.RFC3339),
		Expiry: now.Add(ttl).Format(time.RFC3339), Subject: subj,
	}
	raw, err := signingBytes(c)
	if err != nil {
		return Credential{}, err
	}
	digest := sha256.Sum256(raw)
	c.Proof = base64.RawURLEncoding.EncodeToString(ed25519.Sign(priv, digest[:]))
	return c, nil
}

// Verify checks proof, expiry and the revocation set. `now` is injectable
// for tests and for offline verification with a trusted wall clock.
func Verify(c Credential, pub ed25519.PublicKey, revoked map[string]bool, now time.Time) error {
	if c.Proof == "" {
		return ErrNoProof
	}
	sig, err := base64.RawURLEncoding.DecodeString(c.Proof)
	if err != nil {
		return fmt.Errorf("proof decode: %w", err)
	}
	raw, err := signingBytes(c)
	if err != nil {
		return err
	}
	digest := sha256.Sum256(raw)
	if !ed25519.Verify(pub, digest[:], sig) {
		return ErrBadSig
	}
	exp, err := time.Parse(time.RFC3339, c.Expiry)
	if err != nil {
		return fmt.Errorf("bad expiry: %w", err)
	}
	if now.After(exp) {
		return ErrExpired
	}
	if revoked[c.Subject.LicenceNo] {
		return ErrRevoked
	}
	return nil
}

// RevocationList is the syncable form: one licence number per line.
func ParseRevocations(lines []string) map[string]bool {
	out := map[string]bool{}
	for _, l := range lines {
		if l != "" {
			out[l] = true
		}
	}
	return out
}
