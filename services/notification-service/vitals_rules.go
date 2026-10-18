package main

// Remote patient monitoring (ADR-0017): danger bands for device-fed
// vital-signs Observations. A CHW's BP cuff or a home SpO2 clip posts
// an Observation (own FHIR server, Medplum, or any R4 source); the
// rail reads it here and messages the patient to come in TODAY when
// the reading leaves the safe band. Readings inside the band stay
// silent — the rail never spams a patient for being healthy.
//
// The bands are DATA, not code: VITALS_RULES_FILE (JSON) replaces the
// embedded defaults, so a program's clinical governance tunes thresholds
// without a redeploy. Bands below track WHO/Nigeria primary-care
// referral guidance; the file is the artefact clinicians sign off.

import (
	"encoding/json"
	"os"
)

// VitalRule is one danger band: a reading outside [min, max] breaches.
type VitalRule struct {
	Code  string  `json:"code"`  // LOINC code matched from the Observation coding
	Label string  `json:"label"` // patient-safe name for the SMS ("blood pressure")
	Min   float64 `json:"min"`
	Max   float64 `json:"max"`
	Unit  string  `json:"unit"` // informational; units are not converted
}

// defaultVitalsRules is the embedded JSON — the same shape the file
// takes, so the override path and the default path share one parser.
const defaultVitalsRules = `[
  {"code":"8480-6","label":"blood pressure (systolic)","min":90,"max":160,"unit":"mmHg"},
  {"code":"8462-4","label":"blood pressure (diastolic)","min":50,"max":100,"unit":"mmHg"},
  {"code":"59408-5","label":"oxygen saturation","min":92,"max":100,"unit":"%"},
  {"code":"8867-4","label":"heart rate","min":50,"max":120,"unit":"/min"},
  {"code":"8310-5","label":"temperature","min":35.5,"max":38.5,"unit":"Cel"},
  {"code":"9279-1","label":"respiratory rate","min":10,"max":24,"unit":"/min"},
  {"code":"2339-0","label":"blood glucose","min":3.9,"max":11.0,"unit":"mmol/L"}
]`

// vitalsRules is the loaded band set — a var so tests can replace it.
var vitalsRules = mustRules([]byte(defaultVitalsRules))

func mustRules(raw []byte) []VitalRule {
	rules, err := parseRules(raw)
	if err != nil {
		panic("embedded vitals rules: " + err.Error())
	}
	return rules
}

func parseRules(raw []byte) ([]VitalRule, error) {
	var rules []VitalRule
	if err := json.Unmarshal(raw, &rules); err != nil {
		return nil, err
	}
	for _, r := range rules {
		if r.Code == "" || r.Min >= r.Max {
			return nil, &json.UnmarshalTypeError{Value: "rule", Struct: "VitalRule", Field: r.Code}
		}
	}
	return rules, nil
}

// LoadVitalsRulesFromEnv swaps the band set when VITALS_RULES_FILE is
// set; an unreadable or malformed file fails loudly at boot.
func LoadVitalsRulesFromEnv(getenv func(string) string) error {
	path := getenv("VITALS_RULES_FILE")
	if path == "" {
		return nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	rules, err := parseRules(raw)
	if err != nil {
		return err
	}
	vitalsRules = rules
	return nil
}

// checkVital answers whether this reading breaches its danger band.
// Unknown codes are never alerts — an unconfigured device must not be
// able to page an entire patient panel.
func checkVital(code string, value float64) (VitalRule, bool) {
	for _, r := range vitalsRules {
		if r.Code == code {
			return r, value < r.Min || value > r.Max
		}
	}
	return VitalRule{}, false
}
