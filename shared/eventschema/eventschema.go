// Package eventschema — the contract registry for the platform's event
// envelope (ADR-0013). One envelope ({id, facility, type, ts, payload})
// travels facility edge → LGA hub → state plane; this module defines what
// each event type's payload may carry, so a malformed event is rejected
// at the first tier that sees it instead of travelling 800 km.
//
// Federation semantics (deliberate):
//   - KNOWN type with a wrong-typed or missing-required field → reject.
//   - UNKNOWN type → accept + flag as a warning: the edge may run a newer
//     version than the state plane during a rolling upgrade, and data must
//     never be dropped for being ahead of the registry. Unknown types are
//     counted so ops sees them.
package eventschema

import (
	"fmt"
	"time"
)

// Field is one expected payload member: name + JSON kind.
type Field struct {
	Name string
	Kind string // string | number | bool | array | object
}

// Schema is the contract for one event type.
type Schema struct {
	Fields   []Field
	Required []string
}

var registry = map[string]Schema{
	"encounter":          {Fields: []Field{{"patient_id", "string"}, {"clinician", "string"}, {"protocol", "string"}}},
	"prescription":       {Fields: []Field{{"patient_id", "string"}, {"drug_code", "string"}, {"qty", "number"}, {"pharmacy_id", "string"}}, Required: []string{"patient_id"}},
	"dispensing":         {Fields: []Field{{"patient_id", "string"}, {"drug_code", "string"}, {"qty", "number"}}, Required: []string{"patient_id"}},
	"claim_submitted":    {Fields: []Field{{"claim_id", "string"}, {"amount_ngn", "number"}, {"scheme", "string"}}, Required: []string{"claim_id"}},
	"lab_order":          {Fields: []Field{{"patient_id", "string"}, {"test", "string"}}, Required: []string{"patient_id"}},
	"lab_result":         {Fields: []Field{{"patient_id", "string"}, {"test", "string"}, {"value", "number"}, {"flag", "string"}}, Required: []string{"patient_id"}},
	"vitals":             {Fields: []Field{{"patient_id", "string"}, {"rr", "number"}, {"hr", "number"}, {"spo2", "number"}, {"sbp", "number"}, {"temp", "number"}}, Required: []string{"patient_id"}},
	"referral":           {Fields: []Field{{"patient_id", "string"}, {"to_facility", "string"}, {"urgency", "string"}}, Required: []string{"patient_id"}},
	"emergency":          {Fields: []Field{{"patient_id", "string"}, {"danger", "bool"}}},
	"allergy_alert":      {Fields: []Field{{"patient_id", "string"}, {"allergen", "string"}, {"severity", "string"}}, Required: []string{"patient_id", "allergen"}},
	"appointment":        {Fields: []Field{{"patient_id", "string"}, {"when", "string"}, {"clinic", "string"}}, Required: []string{"patient_id"}},
	"patient_registered": {Fields: []Field{{"patient_id", "string"}, {"kyc_level", "string"}}, Required: []string{"patient_id"}},
	"immunization":       {Fields: []Field{{"patient_id", "string"}, {"vaccine", "string"}, {"dose", "number"}}, Required: []string{"patient_id", "vaccine"}},
	"task_completed":     {Fields: []Field{{"task_id", "string"}, {"chw_id", "string"}}},
	"telemetry":          {Fields: []Field{{"metric", "string"}, {"value", "number"}, {"unit", "string"}}, Required: []string{"metric"}},
	"payment_received":   {Fields: []Field{{"wallet", "string"}, {"amount_ngn", "number"}, {"ref", "string"}}, Required: []string{"amount_ngn"}},
}

// Registered reports whether an event type has a contract.
func Registered(evType string) bool {
	_, ok := registry[evType]
	return ok
}

// Types lists every registered event type (for docs and contract tests).
func Types() []string {
	out := make([]string, 0, len(registry))
	for t := range registry {
		out = append(out, t)
	}
	return out
}

// Validate checks ts format and payload shape for a known type.
// Returns a warning (not an error) for unregistered types.
func Validate(evType, ts string, payload map[string]any) (warning string, err error) {
	if ts != "" {
		if _, perr := time.Parse(time.RFC3339, ts); perr != nil {
			return "", fmt.Errorf("ts %q is not RFC3339", ts)
		}
	}
	schema, ok := registry[evType]
	if !ok {
		return "unregistered event type: " + evType, nil
	}
	for _, name := range schema.Required {
		if _, present := payload[name]; !present {
			return "", fmt.Errorf("%s: required payload field %q missing", evType, name)
		}
	}
	for _, f := range schema.Fields {
		v, present := payload[f.Name]
		if !present {
			continue
		}
		if !kindMatches(v, f.Kind) {
			return "", fmt.Errorf("%s: field %q must be %s", evType, f.Name, f.Kind)
		}
	}
	return "", nil
}

func kindMatches(v any, kind string) bool {
	switch kind {
	case "string":
		_, ok := v.(string)
		return ok
	case "number":
		_, ok := v.(float64) // JSON numbers decode to float64
		return ok
	case "bool":
		_, ok := v.(bool)
		return ok
	case "array":
		_, ok := v.([]any)
		return ok
	case "object":
		_, ok := v.(map[string]any)
		return ok
	}
	return false
}
