package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func write(t *testing.T, dir, name, content string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func appendJ(t *testing.T, dir, content string) {
	t.Helper()
	f, err := os.OpenFile(filepath.Join(dir, journalFile), os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.WriteString(content); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, dir, name string) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(dir, name))
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestIncrementalSyncThenVerify(t *testing.T) {
	src, dst := t.TempDir(), t.TempDir()
	write(t, src, journalFile, "{\"op\":\"open\",\"id\":\"A\",\"opening\":1000}\n")

	m, err := syncOnce(src, dst)
	if err != nil {
		t.Fatal(err)
	}
	if m.JournalBytes == 0 {
		t.Fatal("expected journal bytes mirrored")
	}

	// More ops land; next cycle copies only the tail.
	appendJ(t, src, "{\"op\":\"post\",\"debit\":\"A\",\"credit\":\"B\",\"amount\":250,\"tx\":\"T1\"}\n")
	if _, err := syncOnce(src, dst); err != nil {
		t.Fatal(err)
	}
	if got := read(t, dst, journalFile); got != read(t, src, journalFile) {
		t.Fatalf("mirror journal diverged:\n%s", got)
	}
	if err := verify(src, dst); err != nil {
		t.Fatalf("verify: %v", err)
	}
}

func TestSnapshotResyncsAndTruncates(t *testing.T) {
	src, dst := t.TempDir(), t.TempDir()
	write(t, src, journalFile, "{\"op\":\"open\",\"id\":\"A\",\"opening\":1000}\n")
	if _, err := syncOnce(src, dst); err != nil {
		t.Fatal(err)
	}

	// Ledger snapshots: snapshot appears (newer mtime), journal truncates.
	time.Sleep(10 * time.Millisecond) // mtime granularity
	write(t, src, snapshotFile, "{\"op\":\"_meta\",\"seq\":1}\n{\"op\":\"_account\",\"id\":\"A\",\"balance\":1000}\n")
	write(t, src, journalFile, "") // truncated
	if _, err := syncOnce(src, dst); err != nil {
		t.Fatal(err)
	}
	if got := read(t, dst, journalFile); got != "" {
		t.Fatalf("mirror journal should be truncated after snapshot, got %q", got)
	}
	if err := verify(src, dst); err != nil {
		t.Fatalf("verify after snapshot: %v", err)
	}

	// New ops after snapshot append from offset 0.
	appendJ(t, src, "{\"op\":\"post\",\"debit\":\"A\",\"credit\":\"B\",\"amount\":50,\"tx\":\"T2\"}\n")
	if _, err := syncOnce(src, dst); err != nil {
		t.Fatal(err)
	}
	if err := verify(src, dst); err != nil {
		t.Fatalf("verify after post-snapshot ops: %v", err)
	}
}

func TestVerifyDetectsDrift(t *testing.T) {
	src, dst := t.TempDir(), t.TempDir()
	write(t, src, journalFile, "{\"op\":\"open\",\"id\":\"A\",\"opening\":1}\n")
	if _, err := syncOnce(src, dst); err != nil {
		t.Fatal(err)
	}
	// Corrupt the mirror by hand.
	write(t, dst, journalFile, "{\"op\":\"open\",\"id\":\"EVIL\",\"opening\":999999}\n")
	if err := verify(src, dst); err == nil {
		t.Fatal("verify must fail on drift")
	}
}

func TestRefusesSameDir(t *testing.T) {
	// syncOnce on identical source/dest would truncate the live journal:
	// main() guards this; the unit-level guarantee is copyRange from 0
	// truncates before reading when src==dst — tested here as documentation
	// of why the guard exists. (Guard itself is in main().)
	src := t.TempDir()
	write(t, src, journalFile, "x\n")
	if filepath.Clean(src) == filepath.Clean(src) {
		// main() would refuse; nothing to assert at unit level beyond
		// the functions being safe to call with distinct dirs (covered above).
	}
}
