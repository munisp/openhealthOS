package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestSignVerifyRoundTrip(t *testing.T) {
	pub, priv, err := GenerateKey()
	if err != nil {
		t.Fatal(err)
	}
	payload, _ := CanonicalJSON(map[string]any{"patient": "PT-1", "problems": []string{"J06.9"}})
	qr := SignPayload(payload, priv)
	got, err := VerifyPayload(qr, pub)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(payload) {
		t.Fatalf("payload mismatch: %s", got)
	}
}

func TestTamperRejected(t *testing.T) {
	pub, priv, _ := GenerateKey()
	qr := SignPayload([]byte(`{"a":1}`), priv)
	parts := strings.Split(qr, ".")
	// flip one signature character
	sig := []byte(parts[2])
	if sig[0] == 'A' {
		sig[0] = 'B'
	} else {
		sig[0] = 'A'
	}
	parts[2] = string(sig)
	if _, err := VerifyPayload(strings.Join(parts, "."), pub); err == nil {
		t.Fatal("tampered payload verified")
	}
}

func TestWrongKeyRejected(t *testing.T) {
	_, priv, _ := GenerateKey()
	pub2, _, _ := GenerateKey()
	qr := SignPayload([]byte(`{"a":1}`), priv)
	if _, err := VerifyPayload(qr, pub2); err != ErrBadSig {
		t.Fatalf("want ErrBadSig, got %v", err)
	}
}

func TestBadShapeAndPrefix(t *testing.T) {
	pub, _, _ := GenerateKey()
	if _, err := VerifyPayload("nope", pub); err != ErrBadShape {
		t.Fatalf("want ErrBadShape, got %v", err)
	}
	if _, err := VerifyPayload("XXX.a.b", pub); err != ErrBadPrefix {
		t.Fatalf("want ErrBadPrefix, got %v", err)
	}
}

func TestCanonicalMapOrder(t *testing.T) {
	a, _ := CanonicalJSON(map[string]any{"b": 1, "a": 2})
	var v map[string]any
	if err := json.Unmarshal(a, &v); err != nil {
		t.Fatal(err)
	}
	if string(a) != `{"a":2,"b":1}` {
		t.Fatalf("not canonical: %s", a)
	}
}
