package main

import (
	"bytes"
	"compress/gzip"
	"errors"
	"io"
	"os"
	"path/filepath"
	"testing"
)

// randomBytes returns deterministic low-compressibility data (LCG) so gzip
// size caps are genuinely exercised — repeated patterns would compress to
// nothing and the tests would prove nothing.
func randomBytes(n int, seed uint32) []byte {
	data := make([]byte, n)
	x := seed
	for j := range data {
		x = x*1103515245 + 12345
		data[j] = byte(x >> 16)
	}
	return data
}

func writeEvents(t *testing.T, dir string, sizes ...int) []Event {
	t.Helper()
	for i, sz := range sizes {
		name := filepath.Join(dir, "ev-"+string(rune('a'+i))+".json")
		if err := os.WriteFile(name, randomBytes(sz, uint32(i+1)), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	evs, err := ScanOutbox(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(evs) != len(sizes) {
		t.Fatalf("expected %d events, got %d", len(sizes), len(evs))
	}
	return evs
}

func TestBatchRespectsCompressedCap(t *testing.T) {
	dir := t.TempDir()
	evs := writeEvents(t, dir, 4096, 4096, 4096, 4096, 4096, 4096, 4096, 4096, 4096, 4096)
	payload, included, err := PackBatch(evs, 8192)
	if err != nil {
		t.Fatal(err)
	}
	if len(payload) > 8192 {
		t.Fatalf("payload %d exceeds cap", len(payload))
	}
	if len(included) == 0 || len(included) == len(evs) {
		t.Fatalf("expected a strict subset, got %d of %d", len(included), len(evs))
	}
	// payload must be valid gzip of exactly the included events
	gz, _ := gzip.NewReader(bytes.NewReader(payload))
	raw, _ := io.ReadAll(gz)
	total := int64(0)
	for _, ev := range included {
		total += ev.Size + 1 // +newline
	}
	if int64(len(raw)) != total {
		t.Fatalf("decompressed %d bytes, want %d", len(raw), total)
	}
}

func TestFlushRetainsOutboxOnFailure(t *testing.T) {
	dir := t.TempDir()
	writeEvents(t, dir, 100, 100, 100)
	before, _ := ScanOutbox(dir)
	n, err := FlushOnce(dir, failingSender{})
	if err == nil || n != 0 {
		t.Fatalf("expected failure with 0 sent, got n=%d err=%v", n, err)
	}
	after, _ := ScanOutbox(dir)
	if len(after) != len(before) {
		t.Fatal("failed send must leave the outbox untouched")
	}
}

func TestFlushDrainsOnSuccess(t *testing.T) {
	dir := t.TempDir()
	writeEvents(t, dir, 100, 100, 100)
	n, err := FlushOnce(dir, okSender{})
	if err != nil || n != 3 {
		t.Fatalf("expected 3 sent, got n=%d err=%v", n, err)
	}
	rest, _ := ScanOutbox(dir)
	if len(rest) != 0 {
		t.Fatal("acked events must be removed from the outbox")
	}
}

func TestOversizedSingleEventFailsLoudly(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "big.json")
	os.WriteFile(p, randomBytes(MaxBatchBytes+1, 42), 0o644)
	_, err := FlushOnce(dir, okSender{})
	if err == nil {
		t.Fatal("oversized event must fail loudly, never be silently dropped or split")
	}
}

type okSender struct{}

func (okSender) Send([]byte) error { return nil }

type failingSender struct{}

func (failingSender) Send([]byte) error { return errors.New("offline") }
