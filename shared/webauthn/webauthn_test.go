package webauthn

// Synthetic-authenticator tests: the test plays the role of a platform
// authenticator — building authData, wrapping it in a CBOR attestation
// object, and signing assertions with a real P-256 key — so the full
// verification path (challenge, origin, rpIdHash, COSE key parsing,
// ECDSA signature, sign-count clone detection) runs hermetically.

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"strings"
	"testing"
)

const (
	testRPID      = "portal.openhealth.example"
	testOrigin    = "https://portal.openhealth.example"
	testChallenge = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" // 32 zero bytes, base64url
)

func rpHash() []byte {
	s := sha256.Sum256([]byte(testRPID))
	return s[:]
}

// cborBytes/text/uint/nint/mapHead encode the definite-length CBOR
// subset — enough to build attestation objects and COSE keys.
func cborBytes(b []byte) []byte {
	out := cborHead(2, uint64(len(b)))
	return append(out, b...)
}

func cborText(s string) []byte {
	out := cborHead(3, uint64(len(s)))
	return append(out, s...)
}

func cborUint(v uint64) []byte { return cborHead(0, v) }

func cborNint(v int64) []byte { return cborHead(1, uint64(-1-v)) }

func cborHead(major byte, arg uint64) []byte {
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

func cborMap(pairs ...[]byte) []byte {
	out := cborHead(5, uint64(len(pairs)/2))
	for _, p := range pairs {
		out = append(out, p...)
	}
	return out
}

// coseKey builds the COSE_Key map for a P-256 key.
func coseKey(priv *ecdsa.PrivateKey) []byte {
	x, y := pad32(priv.PublicKey.X.Bytes()), pad32(priv.PublicKey.Y.Bytes())
	return cborMap(
		cborUint(1), cborUint(2), // kty EC2
		cborUint(3), cborNint(-7), // alg ES256
		cborNint(-1), cborUint(1), // crv P-256
		cborNint(-2), cborBytes(x),
		cborNint(-3), cborBytes(y),
	)
}

// regAuthData builds registration authData with attested credential data.
func regAuthData(credID []byte, priv *ecdsa.PrivateKey) []byte {
	out := append([]byte{}, rpHash()...)
	out = append(out, 0x01|0x40) // UP + AT
	out = binary.BigEndian.AppendUint32(out, 0)
	out = append(out, make([]byte, 16)...) // AAGUID
	out = binary.BigEndian.AppendUint16(out, uint16(len(credID)))
	out = append(out, credID...)
	out = append(out, coseKey(priv)...)
	return out
}

func assertAuthData(signCount uint32) []byte {
	out := append([]byte{}, rpHash()...)
	out = append(out, 0x01|0x04) // UP + UV
	return binary.BigEndian.AppendUint32(out, signCount)
}

func clientDataJSON(typ, challenge, origin string) []byte {
	raw, _ := json.Marshal(map[string]string{"type": typ, "challenge": challenge, "origin": origin})
	return raw
}

func attestationObject(authData []byte) []byte {
	return cborMap(
		cborText("fmt"), cborText("none"),
		cborText("attStmt"), cborMap(),
		cborText("authData"), cborBytes(authData),
	)
}

func TestRegistrationRoundTrip(t *testing.T) {
	priv, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	credID := []byte("credential-id-0123456789")
	attObj := attestationObject(regAuthData(credID, priv))
	cd := clientDataJSON("webauthn.create", testChallenge, testOrigin)

	cred, err := ParseRegistration(attObj, cd, testChallenge, []string{testOrigin}, testRPID)
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if cred.ID != base64.RawURLEncoding.EncodeToString(credID) {
		t.Fatalf("cred id mismatch: %q", cred.ID)
	}
	key, err := cred.KeyMaterial()
	if err != nil {
		t.Fatalf("key material: %v", err)
	}
	if key.X.Cmp(priv.PublicKey.X) != 0 || key.Y.Cmp(priv.PublicKey.Y) != 0 {
		t.Fatal("stored key does not rehydrate to the registered key")
	}
}

func TestRegistrationRejects(t *testing.T) {
	priv, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	attObj := attestationObject(regAuthData([]byte("cred-1"), priv))
	good := clientDataJSON("webauthn.create", testChallenge, testOrigin)
	origins := []string{testOrigin}

	if _, err := ParseRegistration(attObj, good, "wrong-challenge", origins, testRPID); err == nil {
		t.Fatal("challenge mismatch must fail")
	}
	if _, err := ParseRegistration(attObj, good, testChallenge, origins, "evil.example"); err == nil {
		t.Fatal("rpIdHash mismatch must fail")
	}
	badOrigin := clientDataJSON("webauthn.create", testChallenge, "https://evil.example")
	if _, err := ParseRegistration(attObj, badOrigin, testChallenge, origins, testRPID); err == nil {
		t.Fatal("foreign origin must fail")
	}
	wrongType := clientDataJSON("webauthn.get", testChallenge, testOrigin)
	if _, err := ParseRegistration(attObj, wrongType, testChallenge, origins, testRPID); err == nil {
		t.Fatal("wrong ceremony type must fail")
	}
	if _, err := ParseRegistration([]byte("not cbor"), good, testChallenge, origins, testRPID); err == nil {
		t.Fatal("garbage attestation must fail")
	}
}

func signAssertion(t *testing.T, priv *ecdsa.PrivateKey, authData, cd []byte) []byte {
	t.Helper()
	sum := sha256.Sum256(cd)
	digest := sha256.Sum256(append(append([]byte{}, authData...), sum[:]...))
	sig, err := ecdsa.SignASN1(rand.Reader, priv, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	return sig
}

func TestAssertionVerifyAndCloneDetection(t *testing.T) {
	priv, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	credID := []byte("cred-assert")
	cred, err := ParseRegistration(
		attestationObject(regAuthData(credID, priv)),
		clientDataJSON("webauthn.create", testChallenge, testOrigin),
		testChallenge, []string{testOrigin}, testRPID)
	if err != nil {
		t.Fatalf("register: %v", err)
	}
	origins := []string{testOrigin}

	auth := assertAuthData(7)
	cd := clientDataJSON("webauthn.get", testChallenge, testOrigin)
	sig := signAssertion(t, priv, auth, cd)
	count, err := VerifyAssertion(cred, auth, cd, sig, testChallenge, origins, testRPID)
	if err != nil || count != 7 {
		t.Fatalf("assertion: count=%d err=%v", count, err)
	}
	// Persist the count, then replay at the same count → clone refused.
	cred.SignCount = count
	if _, err := VerifyAssertion(cred, auth, cd, sig, testChallenge, origins, testRPID); err == nil ||
		!strings.Contains(err.Error(), "sign count") {
		t.Fatalf("replayed sign count must fail, got %v", err)
	}
	// Advancing count passes again.
	auth2 := assertAuthData(8)
	sig2 := signAssertion(t, priv, auth2, cd)
	if _, err := VerifyAssertion(cred, auth2, cd, sig2, testChallenge, origins, testRPID); err != nil {
		t.Fatalf("advancing count must pass: %v", err)
	}
	// A signature from a different key fails.
	other, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	badSig := signAssertion(t, other, auth2, cd)
	if _, err := VerifyAssertion(cred, auth2, cd, badSig, testChallenge, origins, testRPID); err == nil {
		t.Fatal("foreign key signature must fail")
	}
}

func TestCBORRejectsIndefiniteLength(t *testing.T) {
	// 0x9f = indefinite-length array.
	if _, err := cborParse([]byte{0x9f, 0xff}); err == nil {
		t.Fatal("indefinite-length items must be rejected")
	}
}

func TestChallengeFormat(t *testing.T) {
	c := NewChallenge()
	raw, err := base64.RawURLEncoding.DecodeString(c)
	if err != nil || len(raw) != 32 {
		t.Fatalf("challenge must be 32 bytes base64url: %q (%v)", c, err)
	}
	if NewChallenge() == c {
		t.Fatal("challenges must not repeat")
	}
}
