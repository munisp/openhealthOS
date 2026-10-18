// Package main — OpenHealth OS edge sync agent (ADR-0001).
//
// Store-and-forward sync for the facility edge: domain events accumulate in a
// local outbox (one JSON file per event, written by the local data plane);
// the agent packs them into gzip batches capped at MaxBatchBytes
// (<1MB/facility/day budget), POSTs to the state plane ingest endpoint, and
// advances an ACK cursor only on success. Connectivity is an optimisation,
// never a dependency: the facility keeps operating fully offline and the
// outbox simply grows.
//
// Guarantees:
//   - at-least-once delivery (cursor advances only on 2xx) — the ingest side
//     must be idempotent on event id;
//   - bounded payloads (never exceed MaxBatchBytes on the wire);
//   - graceful degradation (any failure leaves the outbox untouched).
package main

import (
	"bytes"
	"compress/gzip"
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/munisp/openhealthOS/shared/sealx"
)

// MaxBatchBytes is the wire cap per batch. The NFR budget is
// <1MB per facility-day; the agent batches hourly at 1/24th of that,
// then flushes the remainder when connectivity returns.
const MaxBatchBytes = 1 << 20 // 1 MiB hard ceiling per batch

// Event is one queued domain event file in the outbox.
type Event struct {
	Path string // file path inside the outbox
	Size int64
}

// ScanOutbox lists pending event files (oldest first by name — filenames
// carry a monotonic timestamp prefix).
func ScanOutbox(dir string) ([]Event, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	names := []string{}
	for _, e := range entries {
		if !e.IsDir() && filepath.Ext(e.Name()) == ".json" {
			names = append(names, e.Name())
		}
	}
	sort.Strings(names)
	events := make([]Event, 0, len(names))
	for _, n := range names {
		fi, err := os.Stat(filepath.Join(dir, n))
		if err != nil {
			continue // vanished between listing and stat — skip, next pass catches up
		}
		events = append(events, Event{Path: filepath.Join(dir, n), Size: fi.Size()})
	}
	return events, nil
}

// PackBatch gzips as many oldest-first events as fit within maxBytes of
// compressed output. Returns the packed payload and the events included.
// The cap is enforced on the compressed payload — what actually goes on
// the wire — never on raw size estimates.
func PackBatch(events []Event, maxBytes int) ([]byte, []Event, error) {
	return PackBatchS(events, maxBytes, nil)
}

// PackBatchS is PackBatch with a sealer: sealed event files (sealx) are
// opened before packing, so the wire format stays plain JSONL regardless
// of at-rest encryption. A tampered or wrongly-keyed file fails the whole
// batch — the outbox keeps everything, and ops sees the error.
func PackBatchS(events []Event, maxBytes int, sealer *sealx.Sealer) ([]byte, []Event, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	included := []Event{}
	for _, ev := range events {
		raw, err := os.ReadFile(ev.Path)
		if err != nil {
			return nil, nil, fmt.Errorf("read %s: %w", ev.Path, err)
		}
		data, err := openMaybeSealed(sealer, raw)
		if err != nil {
			return nil, nil, fmt.Errorf("unseal %s: %w", ev.Path, err)
		}
		if _, err := gz.Write(data); err != nil {
			return nil, nil, err
		}
		gz.Write([]byte("\n"))
		gz.Flush()
		if buf.Len() > maxBytes {
			// Does not fit: drop the last event and re-pack without it.
			// Simpler and safer than trying to surgically unwrite a gzip stream.
			if len(included) == 0 {
				return nil, nil, fmt.Errorf("single event %s exceeds batch cap (%d bytes compressed)", ev.Path, maxBytes)
			}
			return repackS(included, sealer)
		}
		included = append(included, ev)
	}
	if err := gz.Close(); err != nil {
		return nil, nil, err
	}
	if len(included) == 0 {
		return nil, nil, nil
	}
	return buf.Bytes(), included, nil
}

func repack(events []Event) ([]byte, []Event, error) { return repackS(events, nil) }

func repackS(events []Event, sealer *sealx.Sealer) ([]byte, []Event, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	for _, ev := range events {
		raw, err := os.ReadFile(ev.Path)
		if err != nil {
			return nil, nil, err
		}
		data, err := openMaybeSealed(sealer, raw)
		if err != nil {
			return nil, nil, fmt.Errorf("unseal %s: %w", ev.Path, err)
		}
		gz.Write(data)
		gz.Write([]byte("\n"))
	}
	if err := gz.Close(); err != nil {
		return nil, nil, err
	}
	return buf.Bytes(), events, nil
}
