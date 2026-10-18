package main

// Postgres parity: gated on TEST_DATABASE_URL; migrations self-applied
// from db/migrations/0015_dispatch.sql so the suite is hermetic.

import (
	"context"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func pgStore(t *testing.T) *PostgresCaseStore {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL unset")
	}
	_, here, _, _ := runtime.Caller(0)
	mig := filepath.Join(filepath.Dir(here), "..", "..", "db", "migrations", "0015_dispatch.sql")
	sql, err := os.ReadFile(mig)
	if err != nil {
		t.Fatalf("migration: %v", err)
	}
	store, err := NewPostgresCaseStore(context.Background(), dsn)
	if err != nil {
		t.Fatalf("pg: %v", err)
	}
	if _, err := store.pool.Exec(context.Background(), string(sql)); err != nil {
		t.Fatalf("apply migration: %v", err)
	}
	return store
}

func TestPostgresParity(t *testing.T) {
	p := pgStore(t)
	phone := "+234-test-" + time.Now().Format("150405.000000")

	p.UpsertContactOnIntake(phone, "whatsapp", "PT-1", time.Now().UTC().Format(time.RFC3339))
	c, err := p.GetContact(phone)
	if err != nil || !c.ConsentFollowUp {
		t.Fatalf("contact: %v %+v", err, c)
	}

	caseID := p.NextCaseID()
	k := Case{ID: caseID, ContactPhone: phone, Title: "parity", Priority: TriageRed,
		Status: CaseOpen, Owner: "d1", OpenedAt: time.Now().UTC().Format(time.RFC3339),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339)}
	if err := p.PutCase(k); err != nil {
		t.Fatalf("put case: %v", err)
	}
	if _, err := p.OpenCaseFor(phone); err != nil {
		t.Fatalf("open case for: %v", err)
	}

	tk := CaseTask{ID: p.NextTaskID(), CaseID: caseID, Kind: TaskArrivalCheck,
		DueAt: time.Now().UTC().Add(time.Hour).Format(time.RFC3339), Status: TaskOpen}
	if err := p.PutTask(tk); err != nil {
		t.Fatalf("put task: %v", err)
	}
	if got := len(p.OpenTasks()); got < 1 {
		t.Fatalf("open tasks: %d", got)
	}

	if err := p.AddInteraction(Interaction{ID: p.NextInteractionID(), Phone: phone,
		Channel: "whatsapp", Direction: "inbound", Kind: "sos", Summary: "parity",
		Actor: "test", TS: time.Now().UTC().Format(time.RFC3339)}); err != nil {
		t.Fatalf("interaction: %v", err)
	}
	if got := len(p.Interactions(phone)); got != 1 {
		t.Fatalf("interactions: %d", got)
	}

	inc := Incident{ID: p.NextID(), Channel: "app", CallerPhone: phone, Complaint: "bleeding",
		Triage: TriageRed, Status: StTriaged, CreatedAt: time.Now().UTC().Format(time.RFC3339),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339)}
	if err := p.Put(inc); err != nil {
		t.Fatalf("put incident: %v", err)
	}
	if _, err := p.OpenForCaller(phone, time.Now().Add(-time.Hour)); err != nil {
		t.Fatalf("open for caller: %v", err)
	}
	got, err := p.Get(inc.ID)
	if err != nil || got.Complaint != "bleeding" {
		t.Fatalf("get incident: %v %+v", err, got)
	}
}
