// Package sealx — envelope encryption for patient data at rest on edge
// nodes (ADR-0013). Spill files, sync queues and any durable edge buffer
// hold PHI on hardware that sits in unlocked rooms; sealx makes a stolen
// disk a non-event.
//
// Design: AES-256-GCM with a per-installation data key. Sealed payloads
// are self-describing ("SX1" + nonce + ciphertext, base64) so a reader
// always knows whether a line is sealed. Keys come from the environment
// (SEALX_KEY, hex-encoded 32 bytes) or a key file — provisioned by the
// signed OTA channel, never committed, never logged.
//
// stdlib only (crypto/aes, crypto/cipher): no third-party crypto.
package sealx

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
)

// Prefix marks a sealed payload inside an otherwise plain-text stream.
const Prefix = "SX1:"

var (
	ErrBadKey    = errors.New("sealx: key must be 32 bytes (64 hex chars)")
	ErrTampered  = errors.New("sealx: authentication failed — corrupted or wrong key")
	ErrNotSealed = errors.New("sealx: payload is not sealed")
)

// Sealer seals and opens payloads with one data key.
type Sealer struct {
	gcm cipher.AEAD
}

// New builds a Sealer from a raw 32-byte key.
func New(key []byte) (*Sealer, error) {
	if len(key) != 32 {
		return nil, ErrBadKey
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	return &Sealer{gcm: gcm}, nil
}

// FromEnv resolves the data key: SEALX_KEY (hex) wins, then SEALX_KEY_FILE.
// Returns (nil, nil) when neither is set — callers treat that as
// "encryption not provisioned" and stay in plaintext dev mode, loudly.
func FromEnv(get func(string) string) (*Sealer, error) {
	if h := get("SEALX_KEY"); h != "" {
		key, err := hex.DecodeString(strings.TrimSpace(h))
		if err != nil {
			return nil, fmt.Errorf("sealx: SEALX_KEY: %w", err)
		}
		return New(key)
	}
	if f := get("SEALX_KEY_FILE"); f != "" {
		raw, err := os.ReadFile(f)
		if err != nil {
			return nil, fmt.Errorf("sealx: key file: %w", err)
		}
		key, err := hex.DecodeString(strings.TrimSpace(string(raw)))
		if err != nil {
			return nil, fmt.Errorf("sealx: key file hex: %w", err)
		}
		return New(key)
	}
	return nil, nil
}

// Sealed reports whether a payload carries the seal prefix.
func Sealed(payload string) bool { return strings.HasPrefix(payload, Prefix) }

// Seal encrypts plaintext into a self-describing token.
func (s *Sealer) Seal(plaintext []byte) string {
	nonce := make([]byte, s.gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		panic("sealx: CSPRNG unavailable: " + err.Error())
	}
	ct := s.gcm.Seal(nil, nonce, plaintext, nil)
	return Prefix + base64.RawURLEncoding.EncodeToString(append(nonce, ct...))
}

// Open decrypts a sealed token. Any modification — bit flip, truncation,
// wrong key — fails authentication; there is no partial decrypt.
func (s *Sealer) Open(token string) ([]byte, error) {
	if !Sealed(token) {
		return nil, ErrNotSealed
	}
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimPrefix(token, Prefix))
	if err != nil {
		return nil, ErrTampered
	}
	ns := s.gcm.NonceSize()
	if len(raw) < ns+s.gcm.Overhead() {
		return nil, ErrTampered
	}
	pt, err := s.gcm.Open(nil, raw[:ns], raw[ns:], nil)
	if err != nil {
		return nil, ErrTampered
	}
	return pt, nil
}

// SealLine / OpenLine are the JSONL-store helpers: seal a whole line,
// pass through lines that were never sealed (mixed-mode replay after
// enabling encryption on an existing spill).
func (s *Sealer) SealLine(line string) string { return s.Seal([]byte(line)) }

func (s *Sealer) OpenLine(line string) (string, error) {
	if !Sealed(line) {
		return line, nil
	}
	pt, err := s.Open(line)
	return string(pt), err
}
