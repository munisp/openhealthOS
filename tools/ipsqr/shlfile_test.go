package main

import (
	"crypto/rand"
	"strings"
	"testing"

	"github.com/munisp/openhealthOS/shared/shlx"
)

func testKey(t *testing.T) string {
	t.Helper()
	k := make([]byte, 32)
	rand.Read(k)
	return b64(k)
}

func TestSHLRoundtrip(t *testing.T) {
	key := testKey(t)
	env, err := EncryptSHLFile([]byte(`{"resourceType":"Bundle"}`), key)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(env, "OHSHL1.") {
		t.Fatalf("envelope: %s", env[:20])
	}
	pt, err := DecryptSHLFile(env, key)
	if err != nil {
		t.Fatal(err)
	}
	if string(pt) != `{"resourceType":"Bundle"}` {
		t.Fatalf("roundtrip: %s", pt)
	}
}

func TestSHLTamperRejected(t *testing.T) {
	key := testKey(t)
	env, _ := EncryptSHLFile([]byte("secret summary"), key)
	parts := strings.Split(env, ".")
	// flip a ciphertext char
	ct := []byte(parts[2])
	if ct[0] == 'A' {
		ct[0] = 'B'
	} else {
		ct[0] = 'A'
	}
	if _, err := DecryptSHLFile(strings.Join([]string{parts[0], parts[1], string(ct)}, "."), key); err == nil {
		t.Fatal("tampered ciphertext must fail GCM auth")
	}
	// wrong key
	if _, err := DecryptSHLFile(env, testKey(t)); err == nil {
		t.Fatal("wrong key must fail")
	}
	// bad envelope
	if _, err := DecryptSHLFile("OHSHL1.onlytwo", key); err != shlx.ErrEnvelope {
		t.Fatal("malformed envelope must fail closed")
	}
	// short key
	if _, err := EncryptSHLFile([]byte("x"), b64([]byte("short"))); err == nil {
		t.Fatal("short key rejected")
	}
}
