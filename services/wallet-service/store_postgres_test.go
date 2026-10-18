package main

// Postgres idempotency store test — TEST_DATABASE_URL-gated (CI).

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

func TestPostgresIdemStore(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer pool.Close()
	_, file, _, _ := runtime.Caller(0)
	mig := filepath.Join(filepath.Dir(file), "..", "..", "db", "migrations")
	for _, name := range []string{"0001_init.sql", "0006_wallet_idempotency.sql"} {
		sql, rerr := os.ReadFile(filepath.Join(mig, name))
		if rerr != nil {
			t.Fatalf("read %s: %v", name, rerr)
		}
		if _, err := pool.Exec(ctx, string(sql)); err != nil {
			t.Fatalf("apply %s: %v", name, err)
		}
	}
	if _, err := pool.Exec(ctx, "TRUNCATE wallet_idem"); err != nil {
		t.Fatal(err)
	}

	s := NewPostgresIdemStore(pool)
	if _, ok := s.Get("R-1"); ok {
		t.Fatal("unknown ref must miss")
	}
	if err := s.Put("R-1", "PAYOUT-R-1"); err != nil {
		t.Fatal(err)
	}
	tx, ok := s.Get("R-1")
	if !ok || tx != "PAYOUT-R-1" {
		t.Fatalf("roundtrip: %q %v", tx, ok)
	}
	if err := s.Put("R-1", "PAYOUT-OTHER"); err == nil {
		t.Fatal("double Put must violate the PK — silent overwrite hides fraud")
	}
	// the original record stands
	tx, _ = s.Get("R-1")
	if tx != "PAYOUT-R-1" {
		t.Fatalf("record overwritten: %q", tx)
	}
}
