// Package main — OpenHealth OS LGA hub (ADR-0011).
//
// The middle tier between a facility's sync-agent and the state plane.
// A hub sits in the LGA (local government) office or a general hospital —
// physically close to the PHCs it serves, so sync works over marginal
// 2G/3G, and it keeps working toward the state plane when the upstream
// is down (store-and-forward).
//
// Three jobs:
//
//  1. Receive: POST /ingest + /ingest/raw — the same envelope and
//     idempotency contract as the state-plane sync-ingest, so facility
//     agents do not care which tier they are talking to.
//  2. Prioritise: events are classified critical / standard / bulk.
//     When backhaul is scarce, referrals and danger events go first;
//     telemetry waits. Priority is a forwarding policy — everything is
//     always stored durably first.
//  3. Forward: batches drain to the upstream state plane, critical
//     first, with the hub surviving power cuts via a JSONL spill file
//     (replay on boot) or Postgres when DATABASE_URL is set.
//
// A hub also answers local reads (/events, /stats) so LGA dashboards
// work with no upstream at all.
package main

import (
	"sort"
	"strings"
	"sync"
	"time"
)

// Priority classes.
const (
	Critical = "critical" // referrals, danger events — lives, not metrics
	Standard = "standard" // clinical routine + money events
	Bulk     = "bulk"     // telemetry, analytics — never urgent
)

// Event mirrors the sync-ingest envelope exactly (facility agents talk
// to hubs and to the state plane with one client).
type Event struct {
	ID       string         `json:"id"`
	Facility string         `json:"facility"`
	Type     string         `json:"type"`
	TS       string         `json:"ts"`
	Payload  map[string]any `json:"payload,omitempty"`
}

// Classify assigns a forwarding priority. Type drives it; a danger flag
// in the payload always promotes to critical.
func Classify(ev Event) string {
	if danger, _ := ev.Payload["danger"].(bool); danger {
		return Critical
	}
	if strings.Contains(strings.ToLower(ev.Type), "danger") {
		return Critical
	}
	switch ev.Type {
	case "referral", "emergency", "allergy_alert", "referral_accepted", "referral_completed":
		return Critical
	case "encounter", "vitals", "lab_order", "lab_result", "prescription",
		"dispensing", "claim_submitted", "patient_registered", "patient_updated",
		"appointment", "task_completed", "immunization":
		return Standard
	default:
		return Bulk
	}
}

// entry is a buffered event with its class and forwarding state.
type entry struct {
	Ev        Event  `json:"ev"`
	Class     string `json:"class"`
	Forwarded bool   `json:"forwarded"`
	At        string `json:"at"` // hub receive time
}

// Store is the hub persistence seam.
type Store interface {
	// Add buffers the event; returns false if the id was already seen
	// (idempotent — facility agents retry freely).
	Add(ev Event) (bool, error)
	// Pending returns unforwarded events of one class, oldest first.
	Pending(class string, limit int) []entry
	// MarkForwarded acks a batch after the upstream accepted it.
	MarkForwarded(ids []string) error
	// PendingCounts reports unforwarded depth per class.
	PendingCounts() (critical, standard, bulk int)
	Events() []entry
	Close() error
}

var classOrder = []string{Critical, Standard, Bulk}

// ---------- memory store (tests, demos) ----------

type MemoryStore struct {
	mu      sync.Mutex
	seen    map[string]bool
	entries []entry
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{seen: map[string]bool{}}
}

func (m *MemoryStore) Add(ev Event) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.seen[ev.ID] {
		return false, nil
	}
	m.seen[ev.ID] = true
	m.entries = append(m.entries, entry{
		Ev: ev, Class: Classify(ev),
		At: time.Now().UTC().Format(time.RFC3339),
	})
	return true, nil
}

func (m *MemoryStore) Pending(class string, limit int) []entry {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []entry
	for _, e := range m.entries {
		if e.Class == class && !e.Forwarded {
			out = append(out, e)
			if len(out) >= limit {
				break
			}
		}
	}
	return out
}

func (m *MemoryStore) MarkForwarded(ids []string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	ack := make(map[string]bool, len(ids))
	for _, id := range ids {
		ack[id] = true
	}
	for i, e := range m.entries {
		if ack[e.Ev.ID] {
			m.entries[i].Forwarded = true
		}
	}
	return nil
}

func (m *MemoryStore) PendingCounts() (c, s, b int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, e := range m.entries {
		if e.Forwarded {
			continue
		}
		switch e.Class {
		case Critical:
			c++
		case Standard:
			s++
		default:
			b++
		}
	}
	return
}

func (m *MemoryStore) Events() []entry {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]entry, len(m.entries))
	copy(out, m.entries)
	sort.Slice(out, func(i, j int) bool { return out[i].At < out[j].At })
	return out
}

func (m *MemoryStore) Close() error { return nil }
