package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Postgres integration tests. Run only when TEST_DATABASE_URL is set
// (CI provides a postgres:16-alpine service container).
func testPGStore(t *testing.T) *PostgresStore {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL not set; skipping Postgres integration test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer pool.Close()
	_, file, _, _ := runtime.Caller(0)
	mig := filepath.Join(filepath.Dir(file), "..", "..", "db", "migrations")
	for _, name := range []string{"0001_init.sql", "0002_telehealth_equipment.sql", "0003_pharmacy.sql"} {
		sql, err := os.ReadFile(filepath.Join(mig, name))
		if err != nil {
			t.Fatalf("read migration %s: %v", name, err)
		}
		if _, err := pool.Exec(ctx, string(sql)); err != nil {
			t.Fatalf("apply %s: %v", name, err)
		}
	}
	store, err := NewPostgresStore(dsn)
	if err != nil {
		t.Fatalf("NewPostgresStore: %v", err)
	}
	t.Cleanup(store.Close)
	return store
}

func TestPostgresDispenseAtomic(t *testing.T) {
	store := testPGStore(t)
	fac := fmt.Sprintf("PG-FAC-%d", time.Now().UnixNano())

	if _, err := store.UpsertStock(fac, "PARA500", 50, 10); err != nil {
		t.Fatalf("upsert stock: %v", err)
	}
	// Receiving twice accumulates stock.
	if _, err := store.UpsertStock(fac, "PARA500", 50, 10); err != nil {
		t.Fatalf("re-upsert: %v", err)
	}
	lvl, err := store.StockLevel(fac, "PARA500")
	if err != nil || lvl.Quantity != 100 {
		t.Fatalf("stock=%+v err=%v want 100", lvl, err)
	}

	svc := NewService(store)
	svc.now = func() time.Time { return time.Date(2026, 10, 18, 8, 0, 0, 0, time.UTC) }

	req, err := svc.RoutePrescription(DispenseRequest{
		ID:             "rx-pg-1",
		PrescriptionID: "RX-PG-1",
		PatientID:      "PT-PG-1",
		FacilityID:     fac,
		DrugCode:       "PARA500",
		Dose:           "500mg bd x 5d",
		Quantity:       30,
		Prescriber:     "nurse-a",
	})
	if err != nil || req.Status != "pending" {
		t.Fatalf("route: %v %+v", err, req)
	}

	// Duplicate prescription id must be rejected by the unique index.
	if _, err := svc.RoutePrescription(DispenseRequest{
		ID: "rx-pg-2", PrescriptionID: "RX-PG-1", PatientID: "PT-PG-1",
		FacilityID: fac, DrugCode: "PARA500", Quantity: 5,
	}); err == nil {
		t.Fatal("expected duplicate prescription to fail")
	}

	dispensed, draft, err := svc.Dispense("rx-pg-1", "staff-1")
	if err != nil {
		t.Fatalf("dispense: %v", err)
	}
	if dispensed.Status != "dispensed" {
		t.Fatalf("status=%s", dispensed.Status)
	}
	lvl, _ = store.StockLevel(fac, "PARA500")
	if lvl.Quantity != 70 {
		t.Fatalf("stock after dispense=%d want 70", lvl.Quantity)
	}
	if draft.TotalNGN != 750 { // 30 x 25
		t.Fatalf("draft total=%v want 750", draft.TotalNGN)
	}

	// Second dispense of the same request must fail as a bad transition.
	if _, _, err := svc.Dispense("rx-pg-1", "staff-1"); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("double dispense err=%v want ErrBadTransition", err)
	}

	// Allergy-blocked request never touches stock.
	blocked, err := svc.RoutePrescription(DispenseRequest{
		ID: "rx-pg-3", PrescriptionID: "RX-PG-3", PatientID: "PT-PG-2",
		FacilityID: fac, DrugCode: "AMOX500", Quantity: 21, Allergies: []string{"penicillin"},
	})
	if err != nil || blocked.Status != "rejected" {
		t.Fatalf("allergy route: %v %+v", err, blocked)
	}
	if _, _, err := svc.Dispense("rx-pg-3", "staff-1"); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("dispense rejected err=%v", err)
	}
}
