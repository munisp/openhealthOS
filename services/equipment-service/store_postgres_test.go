package main

// Postgres integration test — ticket lifecycle atomicity on the durable
// store. Skipped unless TEST_DATABASE_URL is set.

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

func TestPostgresEquipmentLifecycle(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset — skipping postgres integration test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	svc, err := NewPostgresService(ctx, dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer svc.Close()
	if _, err := svc.pool.Exec(ctx,
		"TRUNCATE equipment, tickets, technicians CASCADE"); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	e, err := svc.Register(Equipment{Facility: "NG-KD-0014", Type: "oxygen-concentrator", Model: "OC-5"})
	if err != nil || e.ID == "" || e.Status != "working" {
		t.Fatalf("register: %v %+v", err, e)
	}
	// Ticket opens and takes the asset down atomically.
	tk, err := svc.OpenTicket(e.ID, "low output pressure", "P2")
	if err != nil {
		t.Fatalf("open ticket: %v", err)
	}
	got, _ := svc.GetEquipment(e.ID)
	if got.Status != "down" {
		t.Fatalf("asset not marked down: %+v", got)
	}
	// Marketplace gate: unvetted tech rejected before any state changes.
	svc.AddTechnician(Technician{ID: "BMET-1", Name: "Musa", Vetted: false})
	if err := svc.Assign(tk.ID, "BMET-1"); !errors.Is(err, ErrUnvettedTech) {
		t.Fatalf("unvetted assign: want ErrUnvettedTech, got %v", err)
	}
	svc.AddTechnician(Technician{ID: "BMET-2", Name: "Ada", Vetted: true})
	if err := svc.Assign(tk.ID, "BMET-2"); err != nil {
		t.Fatalf("assign: %v", err)
	}
	// Asset returns to service atomically with resolution.
	if err := svc.Resolve(tk.ID); err != nil {
		t.Fatalf("resolve: %v", err)
	}
	got, _ = svc.GetEquipment(e.ID)
	if got.Status != "working" {
		t.Fatalf("asset not restored: %+v", got)
	}
	if list := svc.ListTickets("resolved"); len(list) != 1 {
		t.Fatalf("list: %+v", list)
	}
	if _, err := svc.OpenTicket("EQ-XXXXXX", "x", "P3"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("ticket on unknown asset: want ErrNotFound, got %v", err)
	}
}
