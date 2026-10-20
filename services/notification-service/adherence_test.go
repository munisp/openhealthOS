package main

import (
	"strings"
	"sync"
	"testing"
	"time"
)

// captureProvider records every send.
type captureProvider struct {
	mu   sync.Mutex
	sent []struct{ phone, text string }
}

func (c *captureProvider) Name() string { return "capture" }
func (c *captureProvider) Send(phone, text string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.sent = append(c.sent, struct{ phone, text string }{phone, text})
	return nil
}

func adherenceRig(t *testing.T) (*adherence, *captureProvider, *time.Time) {
	t.Helper()
	cp := &captureProvider{}
	svc := NewService(NewMemoryStore(), cp)
	if err := svc.store.PutSubscription(Subscription{PatientID: "PT-1", Phone: "+2348030000000"}); err != nil {
		t.Fatal(err)
	}
	clock := time.Date(2026, 10, 1, 8, 0, 0, 0, time.UTC)
	a := newAdherence(svc)
	a.now = func() time.Time { return clock }
	t.Setenv("ALERT_OPS_CONTACTS", "+2348000000099")
	return a, cp, &clock
}

func TestAdherenceDailyNudgeOncePerDayInMorningWindow(t *testing.T) {
	a, cp, clock := adherenceRig(t)
	a.onPrescription("PT-1", "artemether-lumefantrine")

	*clock = time.Date(2026, 10, 2, 9, 15, 0, 0, time.UTC)
	a.tick()
	a.tick() // same day: must not double-nudge
	if len(cp.sent) != 1 || !strings.Contains(cp.sent[0].text, "artemether-lumefantrine") {
		t.Fatalf("expected exactly one dose nudge, got %+v", cp.sent)
	}
	*clock = time.Date(2026, 10, 3, 9, 5, 0, 0, time.UTC)
	a.tick()
	if len(cp.sent) != 2 {
		t.Fatalf("next day must nudge again, got %d", len(cp.sent))
	}
	*clock = time.Date(2026, 10, 3, 14, 0, 0, 0, time.UTC)
	a.tick()
	if len(cp.sent) != 2 {
		t.Fatalf("outside the 09:00 window no nudge, got %d", len(cp.sent))
	}
}

func TestAdherenceRefillReminderThenLapseEscalation(t *testing.T) {
	a, cp, clock := adherenceRig(t)
	a.onPrescription("PT-1", "amoxicillin")

	// 4 days before run-out: nothing.
	*clock = time.Date(2026, 10, 27, 10, 0, 0, 0, time.UTC)
	a.tick()
	if len(cp.sent) != 0 {
		t.Fatalf("too early for refill reminder: %+v", cp.sent)
	}
	// Inside the 3-day lead: reminder to the patient.
	*clock = time.Date(2026, 10, 29, 10, 0, 0, 0, time.UTC)
	a.tick()
	if len(cp.sent) != 1 || !strings.Contains(cp.sent[0].text, "refill") {
		t.Fatalf("expected refill reminder, got %+v", cp.sent)
	}
	// 3+ days past due, never dispensed: staff escalation, patient not blamed.
	*clock = time.Date(2026, 11, 4, 10, 0, 0, 0, time.UTC)
	a.tick()
	var lapse bool
	for _, m := range cp.sent {
		if m.phone == "+2348000000099" && strings.Contains(m.text, "AdherenceLapse") {
			lapse = true
		}
		if m.phone == "+2348000000099" && strings.Contains(m.text, "time for your medication") {
			t.Fatal("ops contact must not get patient dose nudges")
		}
	}
	if !lapse {
		t.Fatalf("expected lapse escalation to ops, got %+v", cp.sent)
	}
}

func TestAdherenceDispensedStopsLapse(t *testing.T) {
	a, cp, clock := adherenceRig(t)
	a.onPrescription("PT-1", "amoxicillin")
	a.onDispensed("PT-1", "amoxicillin")
	*clock = time.Date(2026, 11, 10, 10, 0, 0, 0, time.UTC)
	a.tick()
	for _, m := range cp.sent {
		if strings.Contains(m.text, "AdherenceLapse") {
			t.Fatal("dispensed course must never lapse")
		}
	}
}

func TestAdherenceRePrescriptionReArms(t *testing.T) {
	a, _, clock := adherenceRig(t)
	a.onPrescription("PT-1", "amoxicillin")
	*clock = clock.Add(10 * 24 * time.Hour)
	a.onPrescription("PT-1", "amoxicillin")
	plans := a.plansFor("PT-1")
	if len(plans) != 1 {
		t.Fatalf("re-prescription must re-arm, not stack: %d plans", len(plans))
	}
	if plans[0].RefillDue.Before(clock.Add(29 * 24 * time.Hour)) {
		t.Fatal("refill due must extend on re-arm")
	}
}
