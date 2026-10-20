package main

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"
)

type fakeNotifier struct{ events []string }

func (f *fakeNotifier) Notify(event string, payload map[string]any) {
	f.events = append(f.events, event)
}

func newSvc() (*Service, *fakeNotifier) {
	n := &fakeNotifier{}
	return NewService(NewMemoryStore(), n), n
}

func create(t *testing.T, svc *Service) Referral {
	t.Helper()
	rec := httptest.NewRecorder()
	body := `{"patient_id":"P-1","from_facility":"PHC-1","to_facility":"GEN-1","reason":"ANC complication","sla_hours":4}`
	svc.Handler().ServeHTTP(rec, httptest.NewRequest("POST", "/referrals", bytes.NewReader([]byte(body))))
	if rec.Code != 201 {
		t.Fatalf("create: %d %s", rec.Code, rec.Body)
	}
	var r Referral
	json.NewDecoder(rec.Body).Decode(&r)
	return r
}

func act(svc *Service, id, action string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	svc.Handler().ServeHTTP(rec, httptest.NewRequest("POST", "/referrals/"+id+"/"+action, nil))
	return rec
}

func TestHappyPathLifecycle(t *testing.T) {
	svc, _ := newSvc()
	r := create(t, svc)
	if r.ID == "" || r.Status != StatusOpen {
		t.Fatalf("bad create: %+v", r)
	}
	if rec := act(svc, r.ID, "accept"); rec.Code != 200 {
		t.Fatalf("accept: %s", rec.Body)
	}
	if rec := act(svc, r.ID, "complete"); rec.Code != 200 {
		t.Fatalf("complete: %s", rec.Body)
	}
	final, _ := svc.Store.Get(r.ID)
	if final.Status != StatusCompleted {
		t.Fatalf("want COMPLETED, got %s", final.Status)
	}
}

func TestIllegalTransitionRejected(t *testing.T) {
	svc, _ := newSvc()
	r := create(t, svc)
	if rec := act(svc, r.ID, "complete"); rec.Code != 409 {
		t.Fatalf("OPEN→COMPLETED must be rejected, got %d", rec.Code)
	}
	act(svc, r.ID, "cancel")
	if rec := act(svc, r.ID, "accept"); rec.Code != 409 {
		t.Fatalf("CANCELLED is terminal, got %d", rec.Code)
	}
}

func TestSweepBreachesAndNotifiesOnce(t *testing.T) {
	svc, n := newSvc()
	svc.Now = func() time.Time { return time.Now().UTC() }
	r := create(t, svc)
	// Age the referral past its 4h SLA by back-dating creation.
	stored, _ := svc.Store.Get(r.ID)
	stored.CreatedAt = time.Now().UTC().Add(-5 * time.Hour)
	svc.Store.Put(stored)
	if marked := svc.Sweep(); marked != 1 {
		t.Fatalf("sweep must mark 1 breach, got %d", marked)
	}
	got, _ := svc.Store.Get(r.ID)
	if got.Status != StatusBreached {
		t.Fatalf("want BREACHED, got %s", got.Status)
	}
	if len(n.events) != 1 || n.events[0] != "referral_breached" {
		t.Fatalf("one breach notification expected: %v", n.events)
	}
	if marked := svc.Sweep(); marked != 0 || len(n.events) != 1 {
		t.Fatal("breach must notify exactly once")
	}
}

func TestFilterByStatus(t *testing.T) {
	svc, _ := newSvc()
	r := create(t, svc)
	act(svc, r.ID, "accept")
	rec := httptest.NewRecorder()
	svc.Handler().ServeHTTP(rec, httptest.NewRequest("GET", "/referrals?status=ACCEPTED", nil))
	var out struct {
		Referrals []Referral `json:"referrals"`
	}
	json.NewDecoder(rec.Body).Decode(&out)
	if len(out.Referrals) != 1 || out.Referrals[0].Status != StatusAccepted {
		t.Fatalf("filter broken: %+v", out)
	}
}
