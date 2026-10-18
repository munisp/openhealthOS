package eventschema

import (
	"strings"
	"testing"
)

func TestKnownTypeValidPayload(t *testing.T) {
	warn, err := Validate("lab_result", "2026-10-18T08:00:00Z",
		map[string]any{"patient_id": "PT-1", "test": "malaria RDT", "value": 1.0})
	if err != nil || warn != "" {
		t.Fatalf("valid: %v %q", err, warn)
	}
}

func TestMissingRequiredRejected(t *testing.T) {
	_, err := Validate("prescription", "2026-10-18T08:00:00Z", map[string]any{"drug_code": "AMOX500"})
	if err == nil || !strings.Contains(err.Error(), "patient_id") {
		t.Fatalf("missing required must reject: %v", err)
	}
}

func TestWrongTypeRejected(t *testing.T) {
	_, err := Validate("vitals", "2026-10-18T08:00:00Z",
		map[string]any{"patient_id": "PT-1", "spo2": "high"})
	if err == nil || !strings.Contains(err.Error(), "number") {
		t.Fatalf("wrong-typed spo2 must reject: %v", err)
	}
}

func TestUnknownTypeWarnsNotDrops(t *testing.T) {
	warn, err := Validate("brand_new_edge_event", "2026-10-18T08:00:00Z", nil)
	if err != nil {
		t.Fatalf("unknown types must not be dropped (rolling upgrades): %v", err)
	}
	if !strings.Contains(warn, "unregistered") {
		t.Fatalf("unknown type must be flagged: %q", warn)
	}
}

func TestBadTimestampRejected(t *testing.T) {
	if _, err := Validate("encounter", "yesterday-ish", nil); err == nil {
		t.Fatal("non-RFC3339 ts must reject")
	}
	if _, err := Validate("encounter", "", nil); err != nil {
		t.Fatalf("absent ts tolerated (structural check owns presence): %v", err)
	}
}

func TestRegistryCoversPlatformTypes(t *testing.T) {
	// The types the platform actually emits (sync tiers, clinical loop,
	// notification rail) must all be registered — a drift here means an
	// unregistered type silently bypassing validation.
	for _, typ := range []string{
		"encounter", "prescription", "dispensing", "claim_submitted",
		"lab_order", "lab_result", "vitals", "referral", "emergency",
		"allergy_alert", "appointment", "patient_registered", "immunization",
		"task_completed", "telemetry", "payment_received",
	} {
		if !Registered(typ) {
			t.Errorf("platform type %q not registered", typ)
		}
	}
}
