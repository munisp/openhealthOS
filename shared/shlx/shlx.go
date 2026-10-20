// Package shlx — SMART Health Links content-file encryption (ADR-0054).
//
// The SHL payload (packages/ips/shl.py) carries a 32-byte content key
// inside the QR link; the file at payload.url is encrypted with that key
// so the hosting server never sees plaintext — the key only travels
// through the QR the patient shows. AES-256-GCM, Go stdlib only.
//
// Envelope: "OHSHL1.<nonce-b64url>.<ciphertext+tag-b64url>" — our own
// compact envelope until a counterparty requires full JWE wrapping
// (documented divergence, ADR-0054). One-shot, whole file in memory:
// IPS summaries are small; this is not the DICOM path.
package shlx

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
)

const EnvelopePrefix = "OHSHL1"

var ErrEnvelope = errors.New("malformed OHSHL1 envelope")

func b64(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }

func unb64(s string) ([]byte, error) { return base64.RawURLEncoding.DecodeString(s) }

// Encrypt encrypts plaintext with the 32-byte base64url key from the SHL
// payload, returning the envelope string to serve at payload.url.
func Encrypt(plaintext []byte, keyB64 string) (string, error) {
	key, err := unb64(keyB64)
	if err != nil || len(key) != 32 {
		return "", fmt.Errorf("SHL key must be 32 bytes base64url: %w", err)
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return "", err
	}
	nonce := make([]byte, gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return "", err
	}
	ct := gcm.Seal(nil, nonce, plaintext, nil)
	return EnvelopePrefix + "." + b64(nonce) + "." + b64(ct), nil
}

// Decrypt reverses Encrypt. Fail-closed: any tamper, truncation, or wrong
// key is an authentication error, never partial data.
func Decrypt(envelope, keyB64 string) ([]byte, error) {
	parts := strings.Split(envelope, ".")
	if len(parts) != 3 || parts[0] != EnvelopePrefix {
		return nil, ErrEnvelope
	}
	key, err := unb64(keyB64)
	if err != nil || len(key) != 32 {
		return nil, errors.New("bad SHL key")
	}
	nonce, err := unb64(parts[1])
	if err != nil {
		return nil, ErrEnvelope
	}
	ct, err := unb64(parts[2])
	if err != nil {
		return nil, ErrEnvelope
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return gcm.Open(nil, nonce, ct, nil) // nil+err on any tamper
}
