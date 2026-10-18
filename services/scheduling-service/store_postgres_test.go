package main

// Postgres parity: the state-plane store must behave exactly like the
// memory store on the invariant paths (no double-book, atomic
// reschedule, FIFO promotion). Skips without TEST_DATABASE_URL; CI
// applies db/migrations/0010_scheduling.sql first.

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

func pgStore(t *testing.T) *PostgresStore {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset — memory store covers this path")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	p, err := NewPostgresStore(ctx, dsn)
	if err != nil {
		t.Fatalf("pg: %v", err)
	}
	// Hermetic per run: wipe the scheduling tables.
	for _, tbl := range []string{"sched_appointments", "sched_waitlist", "sched_slots", "sched_schedules", "sched_seq"} {
		if _, err := p.pool.Exec(context.Background(), "DELETE FROM "+tbl); err != nil {
			t.Fatalf("wipe %s: %v", tbl, err)
		}
	}
	return p
}

func TestPostgresBookingParity(t *testing.T) {
	p := pgStore(t)
	sch, err := p.PutSchedule(Schedule{
		FacilityID: "FAC-PG", ResourceID: "NURSE-1", ResourceName: "Nurse Amina",
		ResourceType: "provider", Service: "general",
		Weekday: 1, StartMin: 480, EndMin: 540, SlotMinutes: 20, Capacity: 1,
	})
	if err != nil {
		t.Fatalf("schedule: %v", err)
	}
	slots, err := p.Generate(sch.ID, "2026-10-19", NOW)
	if err != nil || len(slots) != 3 {
		t.Fatalf("generate: %v %d", err, len(slots))
	}
	a, err := p.Book(slots[0].ID, "PT-1", "fever", map[string]any{"eligible": true}, NOW)
	if err != nil || a.Status != "booked" {
		t.Fatalf("book: %v %+v", err, a)
	}
	if _, err := p.Book(slots[0].ID, "PT-2", "", nil, NOW); !errors.Is(err, ErrSlotFull) {
		t.Fatalf("want ErrSlotFull, got %v", err)
	}
	if _, err := p.Waitlist("PT-2", "FAC-PG", "general", NOW); err != nil {
		t.Fatalf("waitlist: %v", err)
	}
	cancelled, promoted, err := p.Cancel(a.ID, NOW)
	if err != nil || cancelled.Status != "cancelled" {
		t.Fatalf("cancel: %v %+v", err, cancelled)
	}
	if promoted == nil || promoted.PatientID != "PT-2" {
		t.Fatalf("FIFO promotion: %+v", promoted)
	}
	// Coverage snapshot round-trips through JSONB.
	got, err := p.Appointment(a.ID)
	if err != nil || got.Coverage["eligible"] != true {
		t.Fatalf("coverage round-trip: %v %+v", err, got)
	}
	if got.CreatedAt == "" {
		t.Fatalf("created_at must survive the round-trip: %+v", got)
	}
}
