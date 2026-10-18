package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

// Postgres integration test — runs only with TEST_DATABASE_URL (CI).
func TestPostgresNotificationStore(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL not set")
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
	for _, name := range []string{"0001_init.sql", "0002_telehealth_equipment.sql", "0003_pharmacy.sql", "0004_notifications.sql"} {
		sql, err := os.ReadFile(filepath.Join(mig, name))
		if err != nil {
			t.Fatalf("read %s: %v", name, err)
		}
		if _, err := pool.Exec(ctx, string(sql)); err != nil {
			t.Fatalf("apply %s: %v", name, err)
		}
	}

	store, err := NewPostgresStore(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	pid := "PT-PG-" + time.Now().Format("150405.000000")
	if err := store.PutSubscription(Subscription{PatientID: pid, Phone: "+2348000000001", Language: "ha", Facility: "F-1", UpdatedAt: "2026-10-18T09:00:00Z"}); err != nil {
		t.Fatalf("put sub: %v", err)
	}
	sub, err := store.Subscription(pid)
	if err != nil || sub.Phone != "+2348000000001" || sub.Language != "ha" {
		t.Fatalf("get sub: %v %+v", err, sub)
	}
	// upsert changes the number
	if err := store.PutSubscription(Subscription{PatientID: pid, Phone: "+2348000000002", Language: "en", Facility: "F-1", UpdatedAt: "2026-10-18T10:00:00Z"}); err != nil {
		t.Fatal(err)
	}
	sub, _ = store.Subscription(pid)
	if sub.Phone != "+2348000000002" {
		t.Fatalf("upsert phone: %+v", sub)
	}

	n := Notification{ID: "n-pg-1", PatientID: pid, Phone: sub.Phone, Kind: "lab_ready",
		Text: "OpenHealth: result ready", State: StateQueued, CreatedAt: "2026-10-18T09:00:00Z"}
	if err := store.Enqueue(n); err != nil {
		t.Fatalf("enqueue: %v", err)
	}
	if len(store.Pending(10)) != 1 {
		t.Fatal("pending should contain the message")
	}
	attempts, err := store.RecordAttempt("n-pg-1")
	if err != nil || attempts != 1 {
		t.Fatalf("record attempt: %v %d", err, attempts)
	}
	if err := store.MarkSent("n-pg-1", "2026-10-18T09:01:00Z"); err != nil {
		t.Fatalf("mark sent: %v", err)
	}
	if err := store.MarkSent("n-pg-1", "2026-10-18T09:02:00Z"); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("double send guard: %v", err)
	}
	if got := len(store.Pending(10)); got != 0 {
		t.Fatalf("pending after send: %d", got)
	}
	if q, s, f := store.Stats(); q != 0 || s != 1 || f != 0 {
		t.Fatalf("stats: q=%d s=%d f=%d", q, s, f)
	}
	if got := store.ForPatient(pid); len(got) != 1 || got[0].State != StateSent {
		t.Fatalf("for patient: %+v", got)
	}
}

// Passkey Postgres parity (ADR-0017) — credential round-trip and the
// monotonic sign-count guard, gated on TEST_DATABASE_URL like the rest.
func TestPostgresPasskeyStore(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL not set")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer pool.Close()
	_, file, _, _ := runtime.Caller(0)
	sql, err := os.ReadFile(filepath.Join(filepath.Dir(file), "..", "..", "db", "migrations", "0012_passkeys.sql"))
	if err != nil {
		t.Fatalf("read migration: %v", err)
	}
	if _, err := pool.Exec(ctx, string(sql)); err != nil {
		t.Fatalf("apply 0012: %v", err)
	}
	if _, err := pool.Exec(ctx, "TRUNCATE portal_passkeys"); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	store, err := NewPostgresStore(dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	k := PasskeyCredential{CredID: "cred-pg-1", PatientID: "P-PG", PubX: "AAAA", PubY: "BBBB", SignCount: 1,
		CreatedAt: time.Now().UTC().Format(time.RFC3339)}
	if err := store.PutPasskey(k); err != nil {
		t.Fatalf("put: %v", err)
	}
	got, err := store.PasskeyByCredID("cred-pg-1")
	if err != nil || got.PatientID != "P-PG" || got.SignCount != 1 {
		t.Fatalf("get: %v %+v", err, got)
	}
	if n := len(store.Passkeys("P-PG")); n != 1 {
		t.Fatalf("passkeys by patient: %d", n)
	}
	if _, err := store.PasskeyByCredID("nope"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
	// Counter advances; a backwards write is a no-op, never a rollback.
	if err := store.UpdatePasskeySignCount("cred-pg-1", 5); err != nil {
		t.Fatalf("update count: %v", err)
	}
	if err := store.UpdatePasskeySignCount("cred-pg-1", 2); err != nil {
		t.Fatalf("backwards update should be a guarded no-op, got %v", err)
	}
	got, _ = store.PasskeyByCredID("cred-pg-1")
	if got.SignCount != 5 {
		t.Fatalf("monotonic guard broken: %d", got.SignCount)
	}
	if err := store.UpdatePasskeySignCount("nope", 9); !errors.Is(err, ErrNotFound) {
		t.Fatalf("want ErrNotFound, got %v", err)
	}
}
