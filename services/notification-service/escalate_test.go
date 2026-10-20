package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestReferralBreachEscalatesToCareRoomAndOps(t *testing.T) {
	var got map[string]string
	huc := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/escalate" {
			json.NewDecoder(r.Body).Decode(&got)
		}
		w.WriteHeader(200)
	}))
	t.Cleanup(huc.Close)
	t.Setenv("HUC_ADMIN_URL", huc.URL)
	t.Setenv("ALERT_OPS_CONTACTS", "+2348000000099")

	cp := &captureProvider{}
	svc := NewService(NewMemoryStore(), cp)
	_ = svc.store.PutSubscription(Subscription{PatientID: "PT-5", Phone: "+234805"})

	svc.handleReferralBreach(DomainEvent{Type: "referral_breached", Payload: map[string]any{
		"patient_id": "PT-5", "referral_id": "REF-000042", "room_id": "!room:huc",
	}})

	if got["reason"] != "referral_breached:REF-000042" || got["username"] != "+234805" {
		t.Fatalf("care-room escalation: %+v", got)
	}
	if len(cp.sent) != 1 || cp.sent[0].phone != "+2348000000099" {
		t.Fatalf("ops alert: %+v", cp.sent)
	}
}

func TestEscalationFailSoftWithoutHUC(t *testing.T) {
	t.Setenv("HUC_ADMIN_URL", "")
	t.Setenv("ALERT_OPS_CONTACTS", "")
	cp := &captureProvider{}
	svc := NewService(NewMemoryStore(), cp)
	// No panic, no send — the rail simply has nowhere to go.
	svc.handleReferralBreach(DomainEvent{Type: "referral_breached",
		Payload: map[string]any{"patient_id": "GHOST", "referral_id": "REF-1"}})
	if len(cp.sent) != 0 {
		t.Fatalf("nothing should send: %+v", cp.sent)
	}
}
