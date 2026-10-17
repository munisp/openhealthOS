package main

// Postgres integration test — the access rule runs identically over the
// durable backend, and audit survives "restart". Skipped unless
// TEST_DATABASE_URL is set.

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

func TestPostgresBackendAccessRule(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset — skipping postgres integration test")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	be, err := newPostgresBackend(ctx, dsn)
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer be.Close()
	if _, err := be.pool.Exec(ctx, "TRUNCATE fhir_resources, shr_audit"); err != nil {
		t.Fatalf("truncate: %v", err)
	}

	consent := staticConsent{grants: map[string]bool{"P-1|NG-KD-0099": true}}
	store := NewStoreWithBackend(be, consent)
	store.now = func() time.Time { return time.Date(2026, 10, 18, 12, 0, 0, 0, time.UTC) }

	err = store.Put(Resource{ResourceType: "Observation", ID: "obs-pg-1",
		PatientID: "P-1", SourceFacility: "NG-KD-0014",
		Body: []byte(`{"resourceType":"Observation","status":"final"}`)})
	if err != nil {
		t.Fatalf("put: %v", err)
	}

	// Same rule, durable backend: same-facility allow, no-consent deny,
	// consented allow, break-glass flagged.
	if _, err := store.Get("Observation", "obs-pg-1", "NG-KD-0014", false); err != nil {
		t.Fatalf("same-facility: %v", err)
	}
	if _, err := store.Get("Observation", "obs-pg-1", "NG-KD-0055", false); !errors.Is(err, ErrNoConsent) {
		t.Fatalf("no-consent: want ErrNoConsent, got %v", err)
	}
	if _, err := store.Get("Observation", "obs-pg-1", "NG-KD-0099", false); err != nil {
		t.Fatalf("consented: %v", err)
	}
	if _, err := store.Get("Observation", "obs-pg-1", "NG-KD-0055", true); err != nil {
		t.Fatalf("break-glass: %v", err)
	}

	// Restart: new Store over the same backend — audit retained.
	store2 := NewStoreWithBackend(be, consent)
	audit := store2.Audit()
	if len(audit) != 4 {
		t.Fatalf("audit lost across restart: %+v", audit)
	}
	if audit[1].Decision != "denied" || audit[3].Decision != "break-glass" {
		t.Fatalf("audit decisions: %+v", audit)
	}
}
