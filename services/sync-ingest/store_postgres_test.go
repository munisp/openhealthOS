package main

// Postgres integration test — exactly-once across "restarts" (a fresh
// store instance against the same database must still dedup).
// Skipped unless TEST_DATABASE_URL is set.

import (
	"context"
	"os"
	"testing"
	"time"
)

func TestPostgresExactlyOnceAcrossRestart(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset — skipping postgres integration test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	first, err := NewPostgresStore(ctx, dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer first.Close()
	if _, err := first.pool.Exec(ctx, "TRUNCATE sync_events"); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	ev := Event{ID: "EV-1", Facility: "NG-KD-0014", Type: "encounter",
		TS: "2026-10-18T08:00:00Z", Payload: map[string]any{"patient": "OH-NG-KD-0014-000001"}}
	if !first.Apply(ev) {
		t.Fatal("first apply rejected")
	}
	if first.Apply(ev) {
		t.Fatal("duplicate accepted on same store")
	}

	// Simulated restart: new store, same database.
	second, err := NewPostgresStore(ctx, dsn)
	if err != nil {
		t.Fatalf("reconnect: %v", err)
	}
	defer second.Close()
	if !second.Seen("EV-1") {
		t.Fatal("dedup lost across restart")
	}
	if second.Apply(ev) {
		t.Fatal("duplicate accepted after restart — exactly-once broken")
	}
	_, applied, dups := second.Stats()
	if applied != 1 || dups != 1 {
		t.Fatalf("stats: applied=%d dups=%d", applied, dups)
	}
	events := second.Events()
	if len(events) != 1 || events[0].ID != "EV-1" {
		t.Fatalf("events: %+v", events)
	}
}
