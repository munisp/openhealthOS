package main

// Postgres integration test — the nurse-first lifecycle over the durable
// store, including a "restart" mid-flow. Skipped unless TEST_DATABASE_URL.

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"
)

func pgTelehealth(t *testing.T) *PostgresService {
	t.Helper()
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
	t.Cleanup(svc.Close)
	if _, err := svc.pool.Exec(ctx,
		"TRUNCATE consults, referrals, prescriptions CASCADE"); err != nil {
		t.Fatalf("truncate: %v", err)
	}
	return svc
}

func TestPostgresNurseFirstLifecycle(t *testing.T) {
	svc := pgTelehealth(t)
	c := svc.Open("P-1", "NG-KD-0014", "NURSE-7", "fever")
	if c.ID == "" || c.Status != "open" {
		t.Fatalf("open: %+v", c)
	}
	// The gate holds on Postgres too.
	if _, err := svc.Escalate(c.ID); !errors.Is(err, ErrNoAssessment) {
		t.Fatalf("escalate without assessment: want ErrNoAssessment, got %v", err)
	}
	if _, err := svc.Prescribe(c.ID, "NURSE-7", "ACT", "bd", 24, "PH-1"); !errors.Is(err, ErrNotPrescribable) {
		t.Fatalf("prescribe without assessment: want ErrNotPrescribable, got %v", err)
	}
	if _, err := svc.Assess(c.ID, "MALARIA-UNCOMPLICATED", "RDT positive"); err != nil {
		t.Fatalf("assess: %v", err)
	}
	// Guarded transition: a second assess must fail, not silently overwrite.
	if _, err := svc.Assess(c.ID, "OTHER", "x"); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("re-assess: want ErrBadTransition, got %v", err)
	}
	rx, err := svc.Prescribe(c.ID, "NURSE-7", "artemether-lumefantrine", "4bd", 24, "PH-1")
	if err != nil || rx.Status != "routed" {
		t.Fatalf("prescribe: %v %+v", err, rx)
	}
	if _, err := svc.Dispense(rx.ID); err != nil {
		t.Fatalf("dispense: %v", err)
	}
	if _, err := svc.Dispense(rx.ID); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("double dispense: want ErrBadTransition, got %v", err)
	}
	closed, err := svc.CloseRoutine(c.ID)
	if err != nil || closed.Status != "closed" || closed.ClosedAt == "" {
		t.Fatalf("close: %v %+v", err, closed)
	}
}

func TestPostgresReferralLoopSurvivesRestart(t *testing.T) {
	svc := pgTelehealth(t)
	c := svc.Open("P-2", "NG-KD-0014", "NURSE-7", "pregnancy bleeding")
	svc.Assess(c.ID, "OBSTETRIC-DANGER", "APH at 32 weeks")
	svc.Escalate(c.ID)
	ref, err := svc.Refer(c.ID, "NG-KD-GH-01", "emergency", "antepartum haemorrhage")
	if err != nil || ref.Urgency != "emergency" {
		t.Fatalf("refer: %v %+v", err, ref)
	}

	// Restart: new service, same database — the referral is still open.
	dsn := os.Getenv("TEST_DATABASE_URL")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	svc2, err := NewPostgresService(ctx, dsn)
	if err != nil {
		t.Fatalf("reconnect: %v", err)
	}
	defer svc2.Close()
	if _, err := svc2.CompleteReferral(ref.ID); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("complete before accept after restart: want ErrBadTransition, got %v", err)
	}
	if _, err := svc2.AcceptReferral(ref.ID); err != nil {
		t.Fatalf("accept after restart: %v", err)
	}
	done, err := svc2.CompleteReferral(ref.ID)
	if err != nil || done.Status != "completed" {
		t.Fatalf("complete: %v %+v", err, done)
	}
}
