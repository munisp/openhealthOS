package main

// At-rest encryption for the edge outbox (ADR-0013). Event files are
// dropped into the outbox by local writers; the agent's SealOutbox
// maintenance pass seals anything plaintext in place (atomic rename), and
// PackBatch opens sealed files on read — the wire format stays plain
// JSONL inside gzip over HTTPS; sealing protects only the disk.
//
// Mixed mode is first-class: sealed and plaintext files coexist, so
// enabling SEALX_KEY on a running edge node needs no migration.

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/munisp/openhealthOS/shared/sealx"
)

// openMaybeSealed returns plaintext for a possibly-sealed payload.
// A nil sealer passes everything through (dev mode, loudly logged at boot).
func openMaybeSealed(s *sealx.Sealer, data []byte) ([]byte, error) {
	if s != nil && sealx.Sealed(string(data)) {
		return s.Open(string(data))
	}
	return data, nil
}

// SealOutbox seals every plaintext .json event file in place and returns
// how many were sealed. Each file is written to a temp sibling, fsynced,
// and renamed over the original — a power cut mid-pass leaves either the
// old plaintext or the new sealed file, never a torn one (the next pass
// re-seals a torn-write survivor only if it parses as complete).
func SealOutbox(dir string, s *sealx.Sealer) (int, error) {
	if s == nil {
		return 0, nil
	}
	events, err := ScanOutbox(dir)
	if err != nil {
		return 0, err
	}
	sealed := 0
	for _, ev := range events {
		data, err := os.ReadFile(ev.Path)
		if err != nil {
			continue // vanished — next pass
		}
		if sealx.Sealed(string(data)) {
			continue
		}
		tmp := ev.Path + ".seal-tmp"
		if err := os.WriteFile(tmp, []byte(s.SealLine(string(data))), 0o600); err != nil {
			return sealed, fmt.Errorf("seal %s: %w", filepath.Base(ev.Path), err)
		}
		if f, err := os.OpenFile(tmp, os.O_RDWR, 0o600); err == nil {
			f.Sync()
			f.Close()
		}
		if err := os.Rename(tmp, ev.Path); err != nil {
			return sealed, fmt.Errorf("rename %s: %w", filepath.Base(ev.Path), err)
		}
		sealed++
	}
	return sealed, nil
}
