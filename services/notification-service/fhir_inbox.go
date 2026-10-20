package main

// FHIR inbox (ADR-0013): when Medplum is the production FHIR server, its
// Subscriptions POST resources here the moment they're created — the
// notification rail becomes event-driven off the clinical record itself,
// no polling, and it works for any FHIR source (Medplum, OpenMRS FHIR2,
// another state's HIE), not just our own services.
//
//	POST /fhir/inbox  — one FHIR R4 resource per call (Medplum rest-hook)
//
// Mapping is deliberately narrow and clinical-detail-free:
//	Observation (laboratory)   → lab_result   ("your result is ready")
//	MedicationRequest          → prescription ("medication ready for pickup")
//	Appointment                → appointment  (reminder)
// Everything else → 202 ignored.

import (
	"encoding/json"
	"net/http"
	"strings"
)

// patientRefID extracts "PT-1" from a FHIR reference "Patient/PT-1".
func patientRefID(ref string) string {
	if i := strings.LastIndex(ref, "/"); i >= 0 {
		return ref[i+1:]
	}
	return ref
}

// fhirToEvent maps an inbound FHIR resource to a DomainEvent.
// ok=false means the resource is not notification-worthy.
func fhirToEvent(resourceType string, res map[string]any) (DomainEvent, bool) {
	str := func(m map[string]any, k string) string { s, _ := m[k].(string); return s }
	payload := map[string]any{}
	switch resourceType {
	case "Observation":
		// Category decides the rail's answer:
		//   laboratory  → "your result is ready" (lab_result)
		//   vital-signs → silent inside the danger band, urgent come-in
		//                 message outside it (RPM, ADR-0017)
		category := ""
		if cats, ok := res["category"].([]any); ok {
			for _, c := range cats {
				if cm, ok := c.(map[string]any); ok {
					for _, coding := range asMapList(cm["coding"]) {
						if str(coding, "code") == "laboratory" {
							category = "laboratory"
						} else if str(coding, "code") == "vital-signs" {
							category = "vital-signs"
						}
					}
				}
			}
		}
		switch category {
		case "laboratory":
			payload["patient_id"] = patientRefID(refOf(res, "subject"))
			if code, ok := res["code"].(map[string]any); ok {
				payload["test_name"] = str(code, "text")
			}
			return DomainEvent{Type: "lab_result", Payload: payload}, payload["patient_id"] != ""
		case "vital-signs":
			return vitalsToEvent(res, payload)
		}
		return DomainEvent{}, false
	case "MedicationRequest":
		payload["patient_id"] = patientRefID(refOf(res, "subject"))
		if med, ok := res["medicationCodeableConcept"].(map[string]any); ok {
			payload["drug_name"] = str(med, "text")
		}
		return DomainEvent{Type: "prescription", Payload: payload}, payload["patient_id"] != ""
	case "MedicationDispense":
		// Pharmacy hand-over: closes the adherence loop (innovation 2).
		payload["patient_id"] = patientRefID(refOf(res, "subject"))
		if med, ok := res["medicationCodeableConcept"].(map[string]any); ok {
			payload["drug_name"] = str(med, "text")
		}
		return DomainEvent{Type: "medication_dispensed", Payload: payload}, payload["patient_id"] != ""
	case "Appointment":
		if parts, ok := res["participant"].([]any); ok {
			for _, p := range parts {
				if pm, ok := p.(map[string]any); ok {
					if actor, ok := pm["actor"].(map[string]any); ok {
						if ref := str(actor, "reference"); strings.HasPrefix(ref, "Patient/") {
							payload["patient_id"] = patientRefID(ref)
						}
					}
				}
			}
		}
		payload["when"] = str(res, "start")
		return DomainEvent{Type: "appointment", Payload: payload}, payload["patient_id"] != ""
	case "DiagnosticReport":
		// Imaging reports: media[].link.reference "Blob/media-{id}" attaches
		// the artifact for inline delivery (matrix leg); without it the
		// message is the ordinary detail-free "result is ready".
		payload["patient_id"] = patientRefID(refOf(res, "subject"))
		if payload["patient_id"] == "" {
			return DomainEvent{}, false
		}
		if code, ok := res["code"].(map[string]any); ok {
			payload["modality"] = str(code, "text")
		}
		for _, m := range asMapList(res["media"]) {
			if link, ok := m["link"].(map[string]any); ok {
				ref := str(link, "reference")
				if strings.HasPrefix(ref, "Blob/media-") {
					payload["blob_ref"] = "blob://media/" + strings.TrimPrefix(ref, "Blob/media-")
				}
			}
		}
		return DomainEvent{Type: "imaging_result", Payload: payload}, true
	}
	return DomainEvent{}, false
}

// vitalsToEvent applies the RPM danger bands: a breach becomes an
// urgent vitals_danger event carrying only a patient-safe label — the
// raw number never goes over SMS (the channel is not private).
func vitalsToEvent(res map[string]any, payload map[string]any) (DomainEvent, bool) {
	code := ""
	if c, ok := res["code"].(map[string]any); ok {
		for _, coding := range asMapList(c["coding"]) {
			if s, _ := coding["code"].(string); s != "" {
				code = s
				break
			}
		}
	}
	vq, _ := res["valueQuantity"].(map[string]any)
	value, _ := vq["value"].(float64)
	rule, breach := checkVital(code, value)
	if !breach {
		return DomainEvent{}, false // inside the band, or unconfigured code
	}
	payload["patient_id"] = patientRefID(refOf(res, "subject"))
	payload["vital_label"] = rule.Label
	return DomainEvent{Type: "vitals_danger", Payload: payload}, payload["patient_id"] != ""
}

func asMapList(v any) []map[string]any {
	out := []map[string]any{}
	if l, ok := v.([]any); ok {
		for _, item := range l {
			if m, ok := item.(map[string]any); ok {
				out = append(out, m)
			}
		}
	}
	return out
}

func refOf(res map[string]any, key string) string {
	if m, ok := res[key].(map[string]any); ok {
		s, _ := m["reference"].(string)
		return s
	}
	return ""
}

// registerFHIRInbox mounts the Medplum rest-hook endpoint.
func registerFHIRInbox(mux *http.ServeMux, svc *Service) {
	mux.HandleFunc("POST /fhir/inbox", func(w http.ResponseWriter, r *http.Request) {
		var res map[string]any
		if err := json.NewDecoder(r.Body).Decode(&res); err != nil {
			http.Error(w, `{"error":"bad fhir resource"}`, http.StatusBadRequest)
			return
		}
		rt, _ := res["resourceType"].(string)
		ev, ok := fhirToEvent(rt, res)
		if !ok {
			writeJSON(w, http.StatusAccepted, map[string]string{"status": "ignored", "resourceType": rt})
			return
		}
		n, err := svc.HandleEvent(ev)
		switch {
		case err == ErrNoPhone:
			writeJSON(w, http.StatusAccepted, map[string]string{"status": "skipped", "reason": "no subscription"})
		case err != nil:
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
		default:
			writeJSON(w, http.StatusCreated, n)
		}
	})
}
