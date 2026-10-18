// Package main — OpenHealth OS sync ingest (state-plane side of ADR-0001).
//
// Receives gzip batches of JSON-line domain events from edge sync agents.
// The edge guarantees at-least-once delivery; this service guarantees
// idempotency — every event carries an id, and duplicates are acknowledged
// but applied exactly once. Together: no loss, no double-apply, and the
// <15-minute reconciliation SLA is measurable from the ack log.
package main

import (
	"bufio"
	"compress/gzip"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/munisp/openhealthOS/shared/eventschema"
)

// Event is one domain event line inside a sync batch.
type Event struct {
	ID       string         `json:"id"`       // globally unique, set at the edge
	Facility string         `json:"facility"` // registry id of the originating facility
	Type     string         `json:"type"`     // encounter | dispensing | claim_submitted | ...
	TS       string         `json:"ts"`       // ISO timestamp of the event at the edge
	Payload  map[string]any `json:"payload,omitempty"`
}

// Store is the ingest persistence seam: dedup set + applied log.
type Store interface {
	// Seen reports whether the event id has already been applied.
	Seen(id string) bool
	// Apply records the event exactly once. Returns false if already seen.
	Apply(ev Event) bool
	Stats() (received, applied, duplicates int)
	Events() []Event
}

// MemoryStore is the default store (dev, tests); production binds Postgres.
type MemoryStore struct {
	mu         sync.Mutex
	seen       map[string]bool
	events     []Event
	received   int
	duplicates int
}

func NewMemoryStore() *MemoryStore { return &MemoryStore{seen: map[string]bool{}} }

func (m *MemoryStore) Seen(id string) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.seen[id]
}

func (m *MemoryStore) Apply(ev Event) bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.received++
	if m.seen[ev.ID] {
		m.duplicates++
		return false
	}
	m.seen[ev.ID] = true
	m.events = append(m.events, ev)
	return true
}

func (m *MemoryStore) Stats() (int, int, int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.received, len(m.events), m.duplicates
}

func (m *MemoryStore) Events() []Event {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Event, len(m.events))
	copy(out, m.events)
	return out
}

// IngestResult is the ack returned to the sync agent.
type IngestResult struct {
	Received   int      `json:"received"`
	Applied    int      `json:"applied"`
	Duplicates int      `json:"duplicates"`
	Warnings   []string `json:"warnings,omitempty"`
}

// IngestBatch decodes one gzip batch of JSON-line events and applies each
// idempotently. Events without an id are rejected — they cannot be made
// safe under at-least-once delivery.
func IngestBatch(body io.Reader, store Store) (IngestResult, error) {
	gz, err := gzip.NewReader(body)
	if err != nil {
		return IngestResult{}, fmt.Errorf("bad gzip stream: %w", err)
	}
	defer gz.Close()
	return ingestLines(gz, store)
}

// IngestBatchPlain is the uncompressed twin (phones, curl, demos).
func IngestBatchPlain(body io.Reader, store Store) (IngestResult, error) {
	return ingestLines(body, store)
}

func ingestLines(r io.Reader, store Store) (IngestResult, error) {
	var res IngestResult
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 1<<20)
	line := 0
	for sc.Scan() {
		line++
		raw := strings.TrimSpace(sc.Text())
		if raw == "" {
			continue
		}
		var ev Event
		if err := json.Unmarshal([]byte(raw), &ev); err != nil {
			return res, fmt.Errorf("line %d: bad event json: %w", line, err)
		}
		if ev.ID == "" || ev.Facility == "" || ev.Type == "" {
			return res, fmt.Errorf("line %d: event missing id/facility/type", line)
		}
		// Contract registry (ADR-0013): malformed known types are rejected
		// here, at the first state-plane tier — not 800 km downstream.
		// Unregistered types pass with a warning counted in stats (rolling
		// upgrades: edge may be newer than the plane).
		if warn, verr := eventschema.Validate(ev.Type, ev.TS, ev.Payload); verr != nil {
			return res, fmt.Errorf("line %d: %v", line, verr)
		} else if warn != "" {
			res.Warnings = append(res.Warnings, fmt.Sprintf("line %d: %s", line, warn))
		}
		res.Received++
		if store.Apply(ev) {
			res.Applied++
		} else {
			res.Duplicates++
		}
	}
	return res, sc.Err()
}

type server struct {
	store Store
	docs  DocStore
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/ingest", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		res, err := IngestBatch(r.Body, s.store)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(res)
	})
	// Phone/PWA path: plain JSON-lines, no gzip — ancient Android browsers
	// lack CompressionStream. Same idempotent apply, same ack shape.
	mux.HandleFunc("/ingest/raw", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		res, err := IngestBatchPlain(r.Body, s.store)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(res)
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		received, applied, dup := s.store.Stats()
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"events_received": received, "events_applied": applied,
			"duplicates_dropped": dup, "ts": time.Now().UTC().Format(time.RFC3339),
		})
	})
	// /events exports the applied log for the pilot-gates reporter
	// (packages/analytics/reporter.py) — read-only, same seam as /metrics.
	mux.HandleFunc("/events", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(s.store.Events())
	})
	crdtRoutes(mux, s.docs)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}
