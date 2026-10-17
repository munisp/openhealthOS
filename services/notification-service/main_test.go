package main

import (
	"encoding/json"
	"errors"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func testSvc(t *testing.T) (*Service, *LogProvider) {
	t.Helper()
	p := &LogProvider{}
	svc := NewService(NewMemoryStore(), p)
	svc.now = func() time.Time { return time.Date(2026, 10, 18, 9, 0, 0, 0, time.UTC) }
	if err := svc.store.PutSubscription(Subscription{
		PatientID: "PT-1", Phone: "+2348030000000", Language: "en", Facility: "Kawo PHC",
	}); err != nil {
		t.Fatal(err)
	}
	return svc, p
}

func TestAppointmentEventRendersAndDelivers(t *testing.T) {
	svc, p := testSvc(t)
	n, err := svc.HandleEvent(DomainEvent{
		ID: "ev-1", Facility: "F-1", Type: "appointment",
		Payload: map[string]any{
			"patient_id": "PT-1", "facility_name": "Kawo PHC", "when_text": "Tuesday 9am",
		},
	})
	if err != nil || n.ID == "" {
		t.Fatalf("handle: %v %+v", err, n)
	}
	if n.State != StateQueued || n.Kind != "appointment" {
		t.Fatalf("state/kind: %+v", n)
	}
	sent, failed := svc.Drain(10)
	if sent != 1 || failed != 0 {
		t.Fatalf("drain sent=%d failed=%d", sent, failed)
	}
	if len(p.Sent) != 1 || p.Sent[0][0] != "+2348030000000" {
		t.Fatalf("provider sent: %v", p.Sent)
	}
	if !strings.Contains(p.Sent[0][1], "Kawo PHC") {
		t.Fatalf("text missing facility: %q", p.Sent[0][1])
	}
	got, _ := svc.store.Get(n.ID)
	if got.State != StateSent || got.SentAt == "" {
		t.Fatalf("after drain: %+v", got)
	}
	// Double drain never double-sends.
	sent, _ = svc.Drain(10)
	if sent != 0 {
		t.Fatalf("second drain sent=%d", sent)
	}
}

func TestNoSubscriptionSkippedNotFailed(t *testing.T) {
	svc, _ := testSvc(t)
	_, err := svc.HandleEvent(DomainEvent{
		ID: "ev-2", Type: "lab_result",
		Payload: map[string]any{"patient_id": "PT-NOBODY", "test_name": "Malaria RDT"},
	})
	if !errors.Is(err, ErrNoPhone) {
		t.Fatalf("want ErrNoPhone, got %v", err)
	}
	q, _, _ := svc.store.Stats()
	if q != 0 {
		t.Fatalf("queued=%d want 0 — never guess numbers", q)
	}
}

func TestUnknownEventIgnored(t *testing.T) {
	svc, _ := testSvc(t)
	n, err := svc.HandleEvent(DomainEvent{ID: "ev-3", Type: "telemetry", Payload: map[string]any{}})
	if err != nil || n.ID != "" {
		t.Fatalf("unknown event should be ignored: %v %+v", err, n)
	}
}

type flakyProvider struct{ calls int }

func (f *flakyProvider) Send(_, _ string) error { f.calls++; return errors.New("aggregator down") }
func (f *flakyProvider) Name() string           { return "flaky" }

func TestRetriesExhaustToFailed(t *testing.T) {
	p := &flakyProvider{}
	svc := NewService(NewMemoryStore(), p)
	svc.now = func() time.Time { return time.Date(2026, 10, 18, 9, 0, 0, 0, time.UTC) }
	_ = svc.store.PutSubscription(Subscription{PatientID: "PT-1", Phone: "08031112222"})
	_, _ = svc.HandleEvent(DomainEvent{
		ID: "ev-4", Type: "prescription",
		Payload: map[string]any{"patient_id": "PT-1", "drug_name": "AL 80/20"},
	})
	// Every drain retries once; after MaxAttempts the message is failed.
	for i := 0; i < MaxAttempts; i++ {
		svc.Drain(10)
	}
	_, _, failed := svc.store.Stats()
	if failed != 1 {
		t.Fatalf("failed=%d want 1", failed)
	}
	if p.calls != MaxAttempts {
		t.Fatalf("provider calls=%d want %d", p.calls, MaxAttempts)
	}
}

func TestRenderAllKinds(t *testing.T) {
	cases := map[string]map[string]any{
		"appointment":      {"facility_name": "F", "when_text": "Mon 8am"},
		"lab_result":       {"test_name": "Hb", "facility_name": "F"},
		"prescription":     {"drug_name": "ORS", "facility_name": "F"},
		"referral":         {"to_facility": "Gen. Hospital", "status": "accepted"},
		"immunization_due": {"patient_name": "Baby Ada", "vaccine": "Penta2", "facility_name": "F"},
	}
	for typ, payload := range cases {
		kind, text, ok := renderMessage(DomainEvent{Type: typ, Payload: payload})
		if !ok || kind == "" || len(text) < 20 || len(text) > 320 {
			t.Fatalf("%s: kind=%q len=%d ok=%v", typ, kind, len(text), ok)
		}
		if !strings.HasPrefix(text, "OpenHealth:") {
			t.Fatalf("%s: message missing sender prefix: %q", typ, text)
		}
	}
}

func TestNormalizePhone(t *testing.T) {
	if NormalizePhone("0803 111 2222") != "+2348031112222" {
		t.Fatal("0-prefix not normalised")
	}
	if NormalizePhone("+2348031112222") != "+2348031112222" {
		t.Fatal("E.164 must pass through")
	}
}

func TestHTTPSurface(t *testing.T) {
	svc, _ := testSvc(t)
	srv := server{svc: svc}
	ts := httptest.NewServer(srv.routes())
	defer ts.Close()

	// event -> enqueued
	resp, err := ts.Client().Post(ts.URL+"/events", "application/json",
		strings.NewReader(`{"id":"e1","type":"lab_result","payload":{"patient_id":"PT-1","test_name":"Malaria RDT"}}`))
	if err != nil || resp.StatusCode != 201 {
		t.Fatalf("events: %v %d", err, resp.StatusCode)
	}
	resp.Body.Close()

	// drain -> sent
	resp, _ = ts.Client().Post(ts.URL+"/drain", "application/json", nil)
	resp.Body.Close()

	// patient view shows the delivered message
	resp, _ = ts.Client().Get(ts.URL + "/notifications?patient=PT-1")
	var nots []Notification
	if err := json.NewDecoder(resp.Body).Decode(&nots); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if len(nots) != 1 || nots[0].State != StateSent {
		t.Fatalf("patient notifications: %+v", nots)
	}
}
