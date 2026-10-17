package main

// Postgres integration test — artefacts and audit survive "restart".
// Skipped unless TEST_DATABASE_URL is set.

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

func TestPostgresConsentLifecycle(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset — skipping postgres integration test")
	}
	key := []byte("test-key")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	first, err := NewPostgresManager(ctx, dsn, key)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer first.Close()
	if _, err := first.pool.Exec(ctx, "TRUNCATE consents, consent_audit"); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	c, err := first.Issue("OH-NG-KD-0014-000001", "NG-KD-0099", "shr-read", "treatment")
	if err != nil || c.ID == "" || c.Signature == "" {
		t.Fatalf("issue: %v %+v", err, c)
	}
	ok, err := first.Check("OH-NG-KD-0014-000001", "NG-KD-0099", "shr-read")
	if err != nil || !ok {
		t.Fatalf("check: %v %v", err, ok)
	}

	// Simulated restart: new manager, same database, same key.
	second, err := NewPostgresManager(ctx, dsn, key)
	if err != nil {
		t.Fatalf("reconnect: %v", err)
	}
	defer second.Close()
	ok, err = second.Check("OH-NG-KD-0014-000001", "NG-KD-0099", "shr-read")
	if err != nil || !ok {
		t.Fatalf("check after restart: %v %v", err, ok)
	}

	// Revocation persists and takes effect immediately.
	if err := second.Revoke(c.ID); err != nil {
		t.Fatalf("revoke: %v", err)
	}
	ok, err = second.Check("OH-NG-KD-0014-000001", "NG-KD-0099", "shr-read")
	if err != nil || ok {
		t.Fatalf("check after revoke: %v %v", err, ok)
	}
	if err := second.Revoke("CNS-XXXXXX"); !errors.Is(err, ErrNotFound) {
		t.Fatalf("revoke unknown: want ErrNotFound, got %v", err)
	}

	// Tamper detection: corrupt the stored artefact, check must deny loudly.
	tampered := c
	tampered.Scope = "shr-read-all"
	tampered.Signature = "deadbeef"
	if second.Verify(&tampered) {
		t.Fatal("tampered artefact verified")
	}

	// Audit survives restart and records the full chain.
	audit := second.Audit()
	if len(audit) < 4 {
		t.Fatalf("audit short: %+v", audit)
	}
	if audit[0].Event != "ISSUE" {
		t.Fatalf("audit[0]: %+v", audit[0])
	}
}
