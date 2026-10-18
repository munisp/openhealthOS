package main

// RPM danger-band tests (ADR-0017): band loading/override, breach
// detection, the inbox mapping (breach alerts, in-band silence,
// unconfigured devices never page), and the patient-safe rendering.

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func vitalObs(code string, value float64) map[string]any {
	return map[string]any{
		"resourceType": "Observation",
		"category": []any{map[string]any{
			"coding": []any{map[string]any{"code": "vital-signs"}},
		}},
		"code": map[string]any{
			"coding": []any{map[string]any{"code": code}},
		},
		"valueQuantity": map[string]any{"value": value, "unit": "mmHg"},
		"subject":       map[string]any{"reference": "Patient/PT-9"},
	}
}

func TestCheckVitalBands(t *testing.T) {
	if _, breach := checkVital("8480-6", 120); breach {
		t.Fatal("120 systolic is inside the band")
	}
	if r, breach := checkVital("8480-6", 185); !breach || r.Label == "" {
		t.Fatal("185 systolic must breach")
	}
	if _, breach := checkVital("8480-6", 80); !breach {
		t.Fatal("80 systolic must breach the low side")
	}
	if _, breach := checkVital("9999-9", 500); breach {
		t.Fatal("unconfigured codes never alert")
	}
}

func TestVitalsRulesFileOverride(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "rules.json")
	if err := os.WriteFile(path, []byte(`[{"code":"8480-6","label":"bp","min":60,"max":140,"unit":"mmHg"}]`), 0o600); err != nil {
		t.Fatal(err)
	}
	saved := vitalsRules
	defer func() { vitalsRules = saved }()
	if err := LoadVitalsRulesFromEnv(func(k string) string {
		if k == "VITALS_RULES_FILE" {
			return path
		}
		return ""
	}); err != nil {
		t.Fatalf("load: %v", err)
	}
	if _, breach := checkVital("8480-6", 150); !breach {
		t.Fatal("override band must take effect")
	}
	// Malformed file fails loudly.
	bad := filepath.Join(dir, "bad.json")
	os.WriteFile(bad, []byte(`not json`), 0o600)
	if err := LoadVitalsRulesFromEnv(func(k string) string { return bad }); err == nil {
		t.Fatal("malformed rules file must fail the boot")
	}
}

func TestVitalInboxMapping(t *testing.T) {
	// Breach → vitals_danger with the patient-safe label.
	ev, ok := fhirToEvent("Observation", vitalObs("59408-5", 88))
	if !ok || ev.Type != "vitals_danger" {
		t.Fatalf("SpO2 88 must alert: %v %v", ev, ok)
	}
	if ev.Payload["vital_label"] != "oxygen saturation" || ev.Payload["patient_id"] != "PT-9" {
		t.Fatalf("payload: %v", ev.Payload)
	}
	// In-band → ignored.
	if _, ok := fhirToEvent("Observation", vitalObs("59408-5", 97)); ok {
		t.Fatal("SpO2 97 must stay silent")
	}
	// Unconfigured device → ignored.
	if _, ok := fhirToEvent("Observation", vitalObs("xyz-1", 500)); ok {
		t.Fatal("unconfigured device must not alert")
	}
}

func TestVitalsDangerRendersPatientSafe(t *testing.T) {
	kind, text, ok := renderMessage(DomainEvent{Type: "vitals_danger", Payload: map[string]any{
		"vital_label": "oxygen saturation", "facility_name": "Kawo PHC",
	}})
	if !ok || kind == "" {
		t.Fatal("must render")
	}
	if strings.Contains(text, "88") || strings.Contains(text, "%") {
		t.Fatalf("the raw reading never crosses the SMS channel: %q", text)
	}
	if !strings.Contains(text, "oxygen saturation") || !strings.Contains(text, "Kawo PHC") {
		t.Fatalf("text: %q", text)
	}
}
