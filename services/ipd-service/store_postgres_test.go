package main

// Postgres parity (CI: TEST_DATABASE_URL) — self-applies 0013 and runs
// the ADT invariants against the real transactional store.

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func TestPostgresADT(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset — skipping postgres integration test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	store, err := NewPostgresStore(dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer store.Close()
	_, file, _, _ := runtime.Caller(0)
	sql, err := os.ReadFile(filepath.Join(filepath.Dir(file), "..", "..", "db", "migrations", "0013_ipd.sql"))
	if err != nil {
		t.Fatalf("read migration: %v", err)
	}
	if _, err := store.pool.Exec(ctx, string(sql)); err != nil {
		t.Fatalf("apply 0013: %v", err)
	}
	if _, err := store.pool.Exec(ctx, "TRUNCATE ipd_admissions, ipd_beds, ipd_wards, ipd_seq CASCADE"); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	boardWithBeds(t, store)
	a := Admission{ID: store.NextID(), PatientID: "PT-PG", FacilityID: "F-1", WardID: "W-MED",
		BedID: "B-1", Reason: "fracture", Status: AdmOpen, AdmittedAt: time.Now().UTC().Format(time.RFC3339)}
	if err := store.Admit(a); err != nil {
		t.Fatalf("admit: %v", err)
	}
	if err := store.Admit(Admission{ID: "X", PatientID: "PT-PG", BedID: "B-2", WardID: "W-MED",
		Status: AdmOpen}); err != ErrAlreadyAdmitted {
		t.Fatalf("one-open invariant: %v", err)
	}
	if err := store.Admit(Admission{ID: "Y", PatientID: "PT-PG-2", BedID: "B-1", WardID: "W-MED",
		Status: AdmOpen}); err != ErrBedOccupied {
		t.Fatalf("bed exclusivity: %v", err)
	}
	moved, err := store.Transfer(a.ID, "B-2", time.Now().UTC().Format(time.RFC3339))
	if err != nil || moved.BedID != "B-2" || len(moved.Moves) != 1 {
		t.Fatalf("transfer: %v %+v", err, moved)
	}
	if b, _ := store.Bed("B-1"); b.State != BedCleaning {
		t.Fatalf("source bed: %s", b.State)
	}
	dc, err := store.Discharge(a.ID, time.Now().UTC().Format(time.RFC3339), "healed")
	if err != nil || dc.Status != AdmDischarged || dc.DischargedAt == "" {
		t.Fatalf("discharge: %v %+v", err, dc)
	}
	if _, err := store.OpenAdmission("PT-PG"); err != ErrNotFound {
		t.Fatalf("open after discharge: %v", err)
	}
	if got := len(store.Admissions("F-1", "discharged")); got != 1 {
		t.Fatalf("board: %d discharged rows", got)
	}
	if id := store.NextID(); id == a.ID {
		t.Fatalf("ids must advance: %q", id)
	}
}
