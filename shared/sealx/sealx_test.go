package sealx

import (
	"crypto/rand"
	"strings"
	"testing"
)

func testSealer(t *testing.T) *Sealer {
	t.Helper()
	key := make([]byte, 32)
	rand.Read(key)
	s, err := New(key)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestRoundtrip(t *testing.T) {
	s := testSealer(t)
	msg := `{"id":"ev-1","payload":{"patient":"PT-1","diagnosis":"malaria"}}`
	token := s.Seal([]byte(msg))
	if !Sealed(token) || strings.Contains(token, "malaria") {
		t.Fatalf("sealed token leaks plaintext or lacks prefix: %s", token[:20])
	}
	pt, err := s.Open(token)
	if err != nil || string(pt) != msg {
		t.Fatalf("roundtrip: %v %q", err, pt)
	}
}

func TestTamperDetection(t *testing.T) {
	s := testSealer(t)
	token := s.Seal([]byte("sensitive"))
	// Flip one byte deep in the ciphertext.
	raw := []byte(token)
	raw[len(raw)-6] ^= 0xFF
	if _, err := s.Open(string(raw)); err != ErrTampered {
		t.Fatalf("tampered token must fail authentication, got %v", err)
	}
	if _, err := s.Open(token[:len(token)-8]); err != ErrTampered {
		t.Fatalf("truncated token must fail, got %v", err)
	}
}

func TestWrongKey(t *testing.T) {
	a, b := testSealer(t), testSealer(t)
	if _, err := b.Open(a.Seal([]byte("x"))); err != ErrTampered {
		t.Fatalf("wrong key must fail, got %v", err)
	}
}

func TestMixedModeReplay(t *testing.T) {
	s := testSealer(t)
	plain, sealed := `{"ack":"ev-1"}`, s.SealLine(`{"id":"ev-2"}`)
	if got, _ := s.OpenLine(plain); got != plain {
		t.Fatal("plain lines pass through")
	}
	got, err := s.OpenLine(sealed)
	if err != nil || got != `{"id":"ev-2"}` {
		t.Fatalf("sealed line: %v %q", err, got)
	}
}

func TestFromEnv(t *testing.T) {
	if s, err := FromEnv(func(string) string { return "" }); s != nil || err != nil {
		t.Fatal("unset env → nil sealer (plaintext dev mode)")
	}
	if _, err := FromEnv(func(k string) string {
		if k == "SEALX_KEY" {
			return "tooshort"
		}
		return ""
	}); err == nil {
		t.Fatal("bad hex key must error, not silently weaken")
	}
	good := strings.Repeat("ab", 32)
	if s, err := FromEnv(func(k string) string {
		if k == "SEALX_KEY" {
			return good
		}
		return ""
	}); err != nil || s == nil {
		t.Fatalf("valid hex key: %v", err)
	}
}

func TestBadKeyLength(t *testing.T) {
	if _, err := New([]byte("short")); err != ErrBadKey {
		t.Fatalf("want ErrBadKey, got %v", err)
	}
}
