package sealx

import (
	"encoding/hex"
	"strings"
	"testing"
)

const testHex1 = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
const testHex2 = "1f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100"

func TestKeyringSealOpenRoundtrip(t *testing.T) {
	r, err := ParseKeyring("k1=" + testHex1 + ";k0=" + testHex2)
	if err != nil {
		t.Fatal(err)
	}
	if r.CurrentKid() != "k1" {
		t.Fatalf("first entry must be current, got %q", r.CurrentKid())
	}
	token := r.SealLine(`{"patient":"Amina"}`)
	if !strings.HasPrefix(token, PrefixV2+"k1:") {
		t.Fatalf("token must carry current kid: %q", token[:12])
	}
	pt, err := r.OpenLine(token)
	if err != nil || pt != `{"patient":"Amina"}` {
		t.Fatalf("open: %v %q", err, pt)
	}
}

func TestKeyringOpensOldKeysAfterRotation(t *testing.T) {
	old, _ := ParseKeyring("k1=" + testHex1)
	oldToken := old.SealLine("sealed-before-rotation")

	// Rotate: k2 becomes current, k1 stays in the ring.
	rotated, err := ParseKeyring("k2=" + testHex2 + ";k1=" + testHex1)
	if err != nil {
		t.Fatal(err)
	}
	pt, err := rotated.OpenLine(oldToken)
	if err != nil || pt != "sealed-before-rotation" {
		t.Fatalf("old key must keep opening after rotation: %v", err)
	}
	// New writes use k2.
	if got := rotated.SealLine("x"); !strings.HasPrefix(got, PrefixV2+"k2:") {
		t.Fatalf("new writes must use k2: %q", got[:12])
	}
}

func TestKeyringUnknownKidFailsClosed(t *testing.T) {
	r, _ := ParseKeyring("k2=" + testHex2)
	_, err := r.OpenLine(PrefixV2 + "k1:" + "AAAA")
	if err == nil || !strings.Contains(err.Error(), "unknown key id") {
		t.Fatalf("unknown kid must fail closed: %v", err)
	}
}

func TestKeyringLegacyInterop(t *testing.T) {
	legacy, _ := New(mustHex(t, testHex1))
	sx1 := legacy.SealLine("old-format")

	// Ring with keyring + legacy opens both formats.
	r, err := KeyringFromEnv(mapEnv(map[string]string{
		"SEALX_KEYRING": "k2=" + testHex2,
		"SEALX_KEY":     testHex1,
	}))
	if err != nil {
		t.Fatal(err)
	}
	if pt, err := r.OpenLine(sx1); err != nil || pt != "old-format" {
		t.Fatalf("legacy SX1 must open: %v", err)
	}
	if pt, err := r.OpenLine(r.SealLine("new")); err != nil || pt != "new" {
		t.Fatalf("SX2 must open: %v", err)
	}

	// Legacy-only env behaves byte-identically to the old design.
	legacyOnly, err := KeyringFromEnv(mapEnv(map[string]string{"SEALX_KEY": testHex1}))
	if err != nil {
		t.Fatal(err)
	}
	if legacyOnly.CurrentKid() != "" {
		t.Fatal("legacy-only ring has no current kid")
	}
	if got := legacyOnly.SealLine("x"); !Sealed(got) || strings.HasPrefix(got, PrefixV2) {
		t.Fatalf("legacy-only ring must emit SX1: %q", got[:8])
	}

	// Nothing provisioned → nil, nil (plaintext dev mode contract).
	none, err := KeyringFromEnv(mapEnv(map[string]string{}))
	if none != nil || err != nil {
		t.Fatalf("unprovisioned must be nil,nil: %v %v", none, err)
	}

	// SX1 payload with no legacy key provisioned fails closed.
	sx2Only, _ := ParseKeyring("k2=" + testHex2)
	if _, err := sx2Only.OpenLine(sx1); err == nil {
		t.Fatal("SX1 without legacy key must fail")
	}
}

func TestRotateLineResealsWithCurrentKey(t *testing.T) {
	old, _ := ParseKeyring("k1=" + testHex1)
	oldToken := old.SealLine("rotate-me")

	r, _ := ParseKeyring("k2=" + testHex2 + ";k1=" + testHex1)
	newToken, err := r.RotateLine(oldToken)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(newToken, PrefixV2+"k2:") {
		t.Fatalf("rotated line must use current kid: %q", newToken[:12])
	}
	if pt, _ := r.OpenLine(newToken); pt != "rotate-me" {
		t.Fatalf("rotated line content: %q", pt)
	}
	// Idempotent: rotating a current line returns it unchanged.
	again, err := r.RotateLine(newToken)
	if err != nil || again != newToken {
		t.Fatalf("rotate must be idempotent: %v", err)
	}
	// Plaintext gets sealed (adoption sweep).
	sealed, err := r.RotateLine("plain-line")
	if err != nil || !strings.HasPrefix(sealed, PrefixV2+"k2:") {
		t.Fatalf("plaintext must be sealed: %v %q", err, sealed)
	}
	// After dropping k1, the k2 line still opens; k1 lines fail.
	dropped, _ := ParseKeyring("k2=" + testHex2)
	if pt, err := dropped.OpenLine(newToken); err != nil || pt != "rotate-me" {
		t.Fatalf("post-sweep open: %v", err)
	}
	if _, err := dropped.OpenLine(oldToken); err == nil {
		t.Fatal("dropped key must no longer open")
	}
}

func TestParseKeyringRejectsBadSpecs(t *testing.T) {
	for _, spec := range []string{"", "noequals", "k1=zz", "k1=" + testHex1 + ";k1=" + testHex2, "bad:kid=" + testHex1} {
		if _, err := ParseKeyring(spec); err == nil {
			t.Fatalf("spec %q must be rejected", spec)
		}
	}
}

func mustHex(t *testing.T, h string) []byte {
	t.Helper()
	b, err := hex.DecodeString(h)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func mapEnv(m map[string]string) func(string) string {
	return func(k string) string { return m[k] }
}
