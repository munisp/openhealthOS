// Key rotation for sealx (ADR-0015): a keyring of named data keys.
//
// A single SEALX_KEY can never be rotated without orphaning every sealed
// spill line — that was the Security row's gap. The keyring format stamps
// each new payload with the key id that sealed it:
//
//	SX2:<kid>:<base64url(nonce+ct)>
//
// so old payloads keep opening with their original key while all new
// writes use the current one. Rotation is then an operational event,
// not an outage:
//
//  1. provision SEALX_KEYRING="k2=<hex>;k1=<hex>" (first entry = current)
//  2. new writes seal under k2; reads of k1 payloads keep working
//  3. sweep cold files with RotateLine (open with any ring key, reseal
//     with current), then drop k1 from the ring
//
// Legacy SX1 payloads (no kid) open with the legacy SEALX_KEY sealer;
// a ring configured with only SEALX_KEY behaves byte-identically to the
// pre-rotation design.
package sealx

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"strings"
)

// PrefixV2 marks a keyring-sealed payload (carries the key id).
const PrefixV2 = "SX2:"

var ErrUnknownKey = errors.New("sealx: payload sealed with an unknown key id")

// Keyring seals with the current key and opens with any known key.
type Keyring struct {
	current string             // kid for new payloads; "" in legacy mode
	keys    map[string]*Sealer // kid -> sealer (SX2 payloads)
	legacy  *Sealer            // SEALX_KEY sealer for SX1 payloads
}

// ParseKeyring parses "kid=hex;kid=hex" — first entry becomes current.
func ParseKeyring(spec string) (*Keyring, error) {
	r := &Keyring{keys: map[string]*Sealer{}}
	for _, entry := range strings.Split(spec, ";") {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		kid, hexKey, ok := strings.Cut(entry, "=")
		if !ok || strings.TrimSpace(kid) == "" {
			return nil, fmt.Errorf("sealx: keyring entry %q wants kid=hex", entry)
		}
		kid = strings.TrimSpace(kid)
		if strings.Contains(kid, ":") {
			return nil, fmt.Errorf("sealx: kid %q must not contain ':'", kid)
		}
		key, err := hex.DecodeString(strings.TrimSpace(hexKey))
		if err != nil {
			return nil, fmt.Errorf("sealx: keyring kid %q: %w", kid, err)
		}
		s, err := New(key)
		if err != nil {
			return nil, fmt.Errorf("sealx: keyring kid %q: %w", kid, err)
		}
		if _, dup := r.keys[kid]; dup {
			return nil, fmt.Errorf("sealx: duplicate keyring kid %q", kid)
		}
		r.keys[kid] = s
		if r.current == "" {
			r.current = kid
		}
	}
	if len(r.keys) == 0 {
		return nil, fmt.Errorf("sealx: keyring is empty")
	}
	return r, nil
}

// KeyringFromEnv resolves the keyring and legacy sealer:
//
//	SEALX_KEYRING / SEALX_KEYRING_FILE  — "kid=hex;kid=hex", first = current
//	SEALX_KEY / SEALX_KEY_FILE          — legacy single key (opens SX1)
//
// Returns (nil, nil) when nothing is provisioned (plaintext dev mode,
// same contract as FromEnv).
func KeyringFromEnv(get func(string) string) (*Keyring, error) {
	r := &Keyring{keys: map[string]*Sealer{}}

	spec := get("SEALX_KEYRING")
	if spec == "" {
		if f := get("SEALX_KEYRING_FILE"); f != "" {
			raw, err := os.ReadFile(f)
			if err != nil {
				return nil, fmt.Errorf("sealx: keyring file: %w", err)
			}
			spec = string(raw)
		}
	}
	if strings.TrimSpace(spec) != "" {
		parsed, err := ParseKeyring(spec)
		if err != nil {
			return nil, err
		}
		r.current, r.keys = parsed.current, parsed.keys
	}

	legacy, err := FromEnv(get)
	if err != nil {
		return nil, err
	}
	r.legacy = legacy

	if len(r.keys) == 0 && r.legacy == nil {
		return nil, nil
	}
	return r, nil
}

// CurrentKid reports the key id new payloads are sealed with
// ("" in legacy single-key mode).
func (r *Keyring) CurrentKid() string { return r.current }

// Seal encrypts under the current key. Legacy-only rings emit SX1
// tokens, byte-identical to the pre-rotation format.
func (r *Keyring) Seal(plaintext []byte) string {
	if r.current == "" {
		return r.legacy.Seal(plaintext)
	}
	s := r.keys[r.current]
	nonce, ct := s.sealRaw(plaintext)
	return PrefixV2 + r.current + ":" + base64.RawURLEncoding.EncodeToString(append(nonce, ct...))
}

// Open decrypts SX2 (kid dispatch) and SX1 (legacy key) tokens.
func (r *Keyring) Open(token string) ([]byte, error) {
	switch {
	case strings.HasPrefix(token, PrefixV2):
		rest := strings.TrimPrefix(token, PrefixV2)
		kid, b64, ok := strings.Cut(rest, ":")
		if !ok || kid == "" {
			return nil, ErrTampered
		}
		s, known := r.keys[kid]
		if !known {
			return nil, fmt.Errorf("%w: %q", ErrUnknownKey, kid)
		}
		return s.openRaw(b64)
	case Sealed(token):
		if r.legacy == nil {
			return nil, fmt.Errorf("%w: legacy SX1 payload but SEALX_KEY is not provisioned", ErrUnknownKey)
		}
		return r.legacy.Open(token)
	default:
		return nil, ErrNotSealed
	}
}

// SealLine / OpenLine mirror the Sealer JSONL helpers: unsealed lines
// pass through on open (mixed-mode replay).
func (r *Keyring) SealLine(line string) string { return r.Seal([]byte(line)) }

func (r *Keyring) OpenLine(line string) (string, error) {
	if !Sealed(line) && !strings.HasPrefix(line, PrefixV2) {
		return line, nil
	}
	pt, err := r.Open(line)
	return string(pt), err
}

// RotateLine opens a line with whichever key sealed it and reseals with
// the current key. Plaintext lines are sealed (rotation sweeps double as
// encryption adoption sweeps). Already-current lines are returned as-is
// — sweeps are idempotent and cheap.
func (r *Keyring) RotateLine(line string) (string, error) {
	if strings.HasPrefix(line, PrefixV2+r.current+":") && r.current != "" {
		return line, nil
	}
	pt, err := r.OpenLine(line)
	if err != nil {
		return "", err
	}
	return r.SealLine(pt), nil
}

// sealRaw is the Sealer's raw nonce+ciphertext, exposed for the keyring.
func (s *Sealer) sealRaw(plaintext []byte) (nonce, ct []byte) {
	nonce = make([]byte, s.gcm.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		panic("sealx: CSPRNG unavailable: " + err.Error())
	}
	return nonce, s.gcm.Seal(nil, nonce, plaintext, nil)
}

// openRaw decrypts base64url(nonce+ct).
func (s *Sealer) openRaw(b64 string) ([]byte, error) {
	raw, err := base64.RawURLEncoding.DecodeString(b64)
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
