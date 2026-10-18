package main

// At-rest encryption tests (ADR-0013): plaintext → sealed on disk →
// plaintext on the wire; tamper fails closed; mixed mode coexists.

import (
	"bytes"
	"compress/gzip"
	"crypto/rand"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/munisp/openhealthOS/shared/sealx"
)

func testSealer(t *testing.T) *sealx.Sealer {
	t.Helper()
	key := make([]byte, 32)
	rand.Read(key)
	s, err := sealx.New(key)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func writeSealEvents(t *testing.T, dir string, payloads ...string) {
	t.Helper()
	for i, p := range payloads {
		name := filepath.Join(dir, strings.ReplaceAll("2026-10-18T08:00:0#Z.json", "#", string(rune('0'+i))))
		if err := os.WriteFile(name, []byte(p), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func gunzip(t *testing.T, payload []byte) string {
	t.Helper()
	zr, err := gzip.NewReader(bytes.NewReader(payload))
	if err != nil {
		t.Fatal(err)
	}
	out, err := io.ReadAll(zr)
	if err != nil {
		t.Fatal(err)
	}
	return string(out)
}

func TestSealOutboxRoundtrip(t *testing.T) {
	dir := t.TempDir()
	s := testSealer(t)
	writeSealEvents(t, dir, `{"id":"e1","type":"encounter"}`, `{"id":"e2","type":"vitals"}`)

	n, err := SealOutbox(dir, s)
	if err != nil || n != 2 {
		t.Fatalf("seal pass: n=%d err=%v", n, err)
	}
	// On disk: sealed, unreadable.
	raw, _ := os.ReadFile(filepath.Join(dir, "2026-10-18T08:00:00Z.json"))
	if !sealx.Sealed(string(raw)) || strings.Contains(string(raw), "encounter") {
		t.Fatalf("disk must hold sealed ciphertext, got %q", raw[:40])
	}
	// Second pass seals nothing (idempotent).
	if n, _ := SealOutbox(dir, s); n != 0 {
		t.Fatalf("re-seal should be a no-op, sealed %d", n)
	}
	// Packing opens the seal: the wire sees the original plaintext.
	events, _ := ScanOutbox(dir)
	payload, included, err := PackBatchS(events, MaxBatchBytes, s)
	if err != nil || len(included) != 2 {
		t.Fatalf("pack: %v", err)
	}
	wire := gunzip(t, payload)
	if !strings.Contains(wire, `"id":"e1"`) || !strings.Contains(wire, `"id":"e2"`) {
		t.Fatalf("wire lost events: %s", wire)
	}
}

func TestSealOutboxMixedMode(t *testing.T) {
	dir := t.TempDir()
	s := testSealer(t)
	// One sealed, one plaintext (writer not yet migrated) — both pack.
	writeSealEvents(t, dir, `{"id":"e1"}`)
	writeSealEvents(t, dir) // noop, keeps indices clear
	os.WriteFile(filepath.Join(dir, "2026-10-18T08:00:09Z.json"),
		[]byte(s.SealLine(`{"id":"e2"}`)), 0o600)
	events, _ := ScanOutbox(dir)
	payload, _, err := PackBatchS(events, MaxBatchBytes, s)
	if err != nil {
		t.Fatalf("mixed pack: %v", err)
	}
	wire := gunzip(t, payload)
	if !strings.Contains(wire, `"id":"e1"`) || !strings.Contains(wire, `"id":"e2"`) {
		t.Fatalf("mixed wire: %s", wire)
	}
}

func TestTamperedFileFailsClosed(t *testing.T) {
	dir := t.TempDir()
	s := testSealer(t)
	writeSealEvents(t, dir, `{"id":"e1"}`)
	if _, err := SealOutbox(dir, s); err != nil {
		t.Fatal(err)
	}
	// Flip a byte in the ciphertext.
	p := filepath.Join(dir, "2026-10-18T08:00:00Z.json")
	raw, _ := os.ReadFile(p)
	raw[len(raw)-6] ^= 0xFF
	os.WriteFile(p, raw, 0o600)

	events, _ := ScanOutbox(dir)
	if _, _, err := PackBatchS(events, MaxBatchBytes, s); err == nil {
		t.Fatal("tampered file must fail the batch")
	}
	// Outbox retained: nothing was acked or deleted.
	if events, _ := ScanOutbox(dir); len(events) != 1 {
		t.Fatal("outbox must retain the tampered event for investigation")
	}
}

func TestWrongKeyFailsClosed(t *testing.T) {
	dir := t.TempDir()
	s1 := testSealer(t)
	writeSealEvents(t, dir, `{"id":"e1"}`)
	SealOutbox(dir, s1)
	s2 := testSealer(t) // different key
	events, _ := ScanOutbox(dir)
	if _, _, err := PackBatchS(events, MaxBatchBytes, s2); err == nil {
		t.Fatal("wrong key must fail — never silently emit garbage")
	}
}

func TestNilSealerPassthrough(t *testing.T) {
	dir := t.TempDir()
	writeSealEvents(t, dir, `{"id":"e1"}`)
	if n, _ := SealOutbox(dir, nil); n != 0 {
		t.Fatal("nil sealer must not touch the outbox")
	}
	events, _ := ScanOutbox(dir)
	payload, _, err := PackBatchS(events, MaxBatchBytes, nil)
	if err != nil || !strings.Contains(gunzip(t, payload), `"id":"e1"`) {
		t.Fatalf("nil sealer pack: %v", err)
	}
}
