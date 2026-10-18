// ledger-mirror — warm-standby journal replication for the Rust ledger
// (ADR-0015, closing the TigerBeetle-row gap: single-writer durability
// gains a verified, promotable replica without changing the ledger).
//
// The ledger's durability contract (ADR-0007) makes replication simple and
// safe: the journal is the op stream, snapshots compact it, recovery is
// exact. The mirror tails BOTH files to a standby directory — on another
// mount, host, or object-synced path — with these invariants:
//
//  1. Incremental: journal bytes are copied from the last offset; a poll
//     cycle copies only new ops.
//  2. Truncation-aware: when the ledger snapshots, the journal is
//     truncated; the mirror detects shrink/newer-snapshot and resyncs
//     fully (snapshot first, then journal from byte 0).
//  3. Verified: every cycle ends with fsync + a manifest holding SHA-256
//     of both files; `verify` mode re-hashes source and mirror and fails
//     on any drift. A standby you have not verified is a hope, not a copy.
//  4. Promotion-safe: the mirror is byte-identical to the source, and the
//     ledger's own recovery (tested: torn lines, prepared 2PC txns) is
//     the promotion path — point the standby ledger at the mirror dir and
//     it recovers exactly. See docs/runbooks/ledger-dr.md.
//
// Single-writer discipline is unchanged: exactly one ledger writes the
// source; the mirror only reads.
//
// Usage:
//
//	ledger-mirror -source /data/ledger -dest /standby/ledger -interval 2s
//	ledger-mirror -source /data/ledger -dest /standby/ledger -once
//	ledger-mirror -source /data/ledger -dest /standby/ledger -verify
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"time"
)

const (
	snapshotFile = "snapshot.jsonl"
	journalFile  = "journal.jsonl"
	manifestFile = "mirror-manifest.json"
	stateFile    = ".mirror-state.json"
)

type state struct {
	SnapshotModTime time.Time `json:"snapshot_mod_time"`
	JournalOffset   int64     `json:"journal_offset"`
}

type manifest struct {
	SyncedAt      time.Time `json:"synced_at"`
	SnapshotSHA   string    `json:"snapshot_sha256,omitempty"`
	SnapshotBytes int64     `json:"snapshot_bytes,omitempty"`
	JournalSHA    string    `json:"journal_sha256"`
	JournalBytes  int64     `json:"journal_bytes"`
}

func sha256File(path string) (string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer f.Close()
	h := sha256.New()
	n, err := io.Copy(h, f)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(h.Sum(nil)), n, nil
}

// copyRange writes src[off:] to dst (truncating dst first when off == 0,
// appending otherwise) and fsyncs. Returns the new logical length.
func copyRange(src, dst string, off int64) (int64, error) {
	s, err := os.Open(src)
	if err != nil {
		return 0, err
	}
	defer s.Close()
	info, err := s.Stat()
	if err != nil {
		return 0, err
	}
	if off > info.Size() {
		return 0, fmt.Errorf("offset %d beyond source size %d", off, info.Size())
	}
	if _, err := s.Seek(off, io.SeekStart); err != nil {
		return 0, err
	}
	var d *os.File
	if off == 0 {
		d, err = os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	} else {
		d, err = os.OpenFile(dst, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	}
	if err != nil {
		return 0, err
	}
	defer d.Close()
	if _, err := io.Copy(d, s); err != nil {
		return 0, err
	}
	if err := d.Sync(); err != nil { // fsync: a mirrored byte is durable or it is nothing
		return 0, err
	}
	return info.Size(), nil
}

func loadState(dest string) state {
	var st state
	if raw, err := os.ReadFile(filepath.Join(dest, stateFile)); err == nil {
		_ = json.Unmarshal(raw, &st)
	}
	return st
}

func saveState(dest string, st state) error {
	raw, err := json.Marshal(st)
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(dest, stateFile), raw, 0o644)
}

// syncOnce runs one replication cycle. Exported behaviour documented above.
func syncOnce(source, dest string) (manifest, error) {
	var m manifest
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return m, err
	}
	st := loadState(dest)

	// 1. Snapshot: copy when newer than what we last mirrored.
	srcSnap := filepath.Join(source, snapshotFile)
	if info, err := os.Stat(srcSnap); err == nil {
		if info.ModTime().After(st.SnapshotModTime) {
			if _, err := copyRange(srcSnap, filepath.Join(dest, snapshotFile), 0); err != nil {
				return m, fmt.Errorf("snapshot: %w", err)
			}
			st.SnapshotModTime = info.ModTime()
			st.JournalOffset = 0 // a fresh snapshot truncates the journal
		}
	}

	// 2. Journal: incremental, truncation-aware.
	srcJrn := filepath.Join(source, journalFile)
	info, err := os.Stat(srcJrn)
	if err != nil {
		// No journal yet (ledger idle or pre-first-op): mirror the absence.
		st.JournalOffset = 0
	} else {
		if info.Size() < st.JournalOffset {
			st.JournalOffset = 0 // truncated without a seen snapshot: full resync
		}
		newLen, err := copyRange(srcJrn, filepath.Join(dest, journalFile), st.JournalOffset)
		if err != nil {
			return m, fmt.Errorf("journal: %w", err)
		}
		st.JournalOffset = newLen
	}

	// 3. Manifest: hash what we WROTE, not what we hope we wrote.
	if s, n, err := sha256File(filepath.Join(dest, snapshotFile)); err == nil {
		m.SnapshotSHA, m.SnapshotBytes = s, n
	}
	if s, n, err := sha256File(filepath.Join(dest, journalFile)); err == nil {
		m.JournalSHA, m.JournalBytes = s, n
	} else {
		return m, fmt.Errorf("journal hash: %w", err)
	}
	m.SyncedAt = time.Now().UTC()
	raw, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return m, err
	}
	if err := os.WriteFile(filepath.Join(dest, manifestFile), raw, 0o644); err != nil {
		return m, err
	}
	return m, saveState(dest, st)
}

// verify re-hashes source and mirror and reports drift.
func verify(source, dest string) error {
	for _, f := range []string{snapshotFile, journalFile} {
		sp := filepath.Join(source, f)
		dp := filepath.Join(dest, f)
		sHash, _, sErr := sha256File(sp)
		dHash, _, dErr := sha256File(dp)
		if os.IsNotExist(sErr) && os.IsNotExist(dErr) {
			continue // both absent: consistent
		}
		if sErr != nil || dErr != nil {
			return fmt.Errorf("%s: source err=%v mirror err=%v", f, sErr, dErr)
		}
		if sHash != dHash {
			return fmt.Errorf("%s: DRIFT — source and mirror differ", f)
		}
	}
	return nil
}

func main() {
	source := flag.String("source", "", "ledger data dir (LEDGER_DATA_DIR of the primary)")
	dest := flag.String("dest", "", "standby mirror dir (another mount/host)")
	interval := flag.Duration("interval", 2*time.Second, "poll interval")
	once := flag.Bool("once", false, "single sync cycle, then exit")
	verifyOnly := flag.Bool("verify", false, "verify mirror against source, then exit")
	flag.Parse()
	if *source == "" || *dest == "" {
		log.Fatal("ledger-mirror: -source and -dest are required")
	}
	if filepath.Clean(*source) == filepath.Clean(*dest) {
		log.Fatal("ledger-mirror: source and dest must differ — mirroring onto yourself is not replication")
	}

	if *verifyOnly {
		if err := verify(*source, *dest); err != nil {
			log.Fatalf("VERIFY FAIL: %v", err)
		}
		log.Printf("VERIFY OK: mirror %s is byte-identical to %s", *dest, *source)
		return
	}

	for {
		m, err := syncOnce(*source, *dest)
		if err != nil {
			log.Printf("sync error: %v (will retry)", err)
		} else if *once {
			log.Printf("synced: journal=%dB sha=%s…", m.JournalBytes, m.JournalSHA[:12])
		}
		if *once {
			return
		}
		time.Sleep(*interval)
	}
}
