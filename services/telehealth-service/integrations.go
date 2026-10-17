// Integrations — the telehealth side of the clinical loop.
//
// On close/escalate the consult becomes a FHIR Encounter in the SHR;
// on prescribe a FHIR MedicationRequest plus a dispense request to the
// pharmacy. Both are best-effort against their URLs: care never blocks
// on an integration, and the pull fallback (GET /prescriptions?status=
// routed) lets the pharmacy catch up after an outage.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"
)

// SHRWriter persists a FHIR resource into the shared health record.
type SHRWriter interface {
	Put(patientID, facilityID, resourceType, id string, fhirBody any) error
}

// PharmacyRouter hands a prescription to the pharmacy for dispensing.
type PharmacyRouter interface {
	Route(req PharmacyDispenseRequest) error
}

// PharmacyDispenseRequest mirrors pharmacy-service's DispenseRequest.
type PharmacyDispenseRequest struct {
	PrescriptionID string   `json:"prescription_id"`
	PatientID      string   `json:"patient_id"`
	FacilityID     string   `json:"facility_id"`
	DrugCode       string   `json:"drug_code"`
	Dose           string   `json:"dose"`
	Quantity       int      `json:"quantity"`
	Prescriber     string   `json:"prescriber"`
	Allergies      []string `json:"allergies,omitempty"`
}

// ---------- HTTP implementations ----------

type httpSHR struct {
	base   string
	client *http.Client
}

func newHTTPClient() *http.Client { return &http.Client{Timeout: 3 * time.Second} }

func (h httpSHR) Put(patientID, facilityID, resourceType, id string, fhirBody any) error {
	raw, err := json.Marshal(fhirBody)
	if err != nil {
		return err
	}
	envelope := map[string]any{
		"resourceType":    resourceType,
		"id":              id,
		"patient_id":      patientID,
		"source_facility": facilityID,
		"body":            json.RawMessage(raw),
	}
	payload, _ := json.Marshal(envelope)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPut,
		fmt.Sprintf("%s/fhir/%s/%s", h.base, resourceType, id), bytes.NewReader(payload))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Facility-ID", facilityID) // provenance + consent-gate context
	resp, err := h.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("shr put %s/%s: %s", resourceType, id, resp.Status)
	}
	return nil
}

type httpPharmacy struct {
	base   string
	client *http.Client
}

func (h httpPharmacy) Route(req PharmacyDispenseRequest) error {
	payload, _ := json.Marshal(req)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	r, err := http.NewRequestWithContext(ctx, http.MethodPost, h.base+"/dispense-requests", bytes.NewReader(payload))
	if err != nil {
		return err
	}
	r.Header.Set("Content-Type", "application/json")
	resp, err := h.client.Do(r)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		return fmt.Errorf("pharmacy route %s: %s", req.PrescriptionID, resp.Status)
	}
	return nil
}

// Integrations bundles the optional downstreams. Nil fields = disabled
// (edge deployments run telehealth with neither SHR nor pharmacy URL).
type Integrations struct {
	shr      SHRWriter
	pharmacy PharmacyRouter
}

// IntegrationsFromEnv wires SHR_URL and PHARMACY_URL when present.
func IntegrationsFromEnv(getenv func(string) string) Integrations {
	ix := Integrations{}
	if u := getenv("SHR_URL"); u != "" {
		ix.shr = httpSHR{base: u, client: newHTTPClient()}
	}
	if u := getenv("PHARMACY_URL"); u != "" {
		ix.pharmacy = httpPharmacy{base: u, client: newHTTPClient()}
	}
	return ix
}

// ---------- FHIR R4 mapping ----------

// ConsultToFHIREncounter maps a finished consult onto the platform's
// Encounter profile (fhir/profiles/openhealth-encounter.json).
func ConsultToFHIREncounter(c Consult) map[string]any {
	status := "finished"
	if c.Status != "closed" && c.Status != "escalated" {
		status = "in-progress"
	}
	return map[string]any{
		"resourceType": "Encounter",
		"id":           c.ID,
		"status":       status,
		"class":        map[string]string{"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": "AMB"},
		"subject":      map[string]string{"reference": "Patient/" + c.PatientID},
		"serviceProvider": map[string]string{
			"reference": "Location/" + c.FacilityID,
		},
		"period":     map[string]string{"start": c.CreatedAt, "end": c.ClosedAt},
		"reasonCode": []map[string]any{{"text": c.Complaint}},
		"extension": []map[string]any{{
			"url":         "https://openhealthos.org/fhir/StructureDefinition/encounter-protocol",
			"valueString": c.Protocol,
		}, {
			"url":         "https://openhealthos.org/fhir/StructureDefinition/encounter-nurse-assessment",
			"valueString": c.Assessment,
		}},
	}
}

// PrescriptionToFHIRMedicationRequest maps a routed prescription.
func PrescriptionToFHIRMedicationRequest(p Prescription, patientID, facilityID string) map[string]any {
	return map[string]any{
		"resourceType": "MedicationRequest",
		"id":           p.ID,
		"status":       "active",
		"intent":       "order",
		"medicationCodeableConcept": map[string]any{
			"text":   p.Medication,
			"coding": []map[string]string{{"code": p.Medication, "display": p.Medication}},
		},
		"subject":   map[string]string{"reference": "Patient/" + patientID},
		"requester": map[string]string{"reference": "Practitioner/" + p.Prescriber},
		"supportingInformation": []map[string]string{{
			"reference": "Encounter/" + p.ConsultID,
		}},
		"dosageInstruction": []map[string]any{{
			"text": p.Dose,
		}},
		"dispenseRequest": map[string]any{
			"quantity": map[string]any{"value": p.Quantity},
			"performer": map[string]string{
				"reference": "Location/" + p.PharmacyID,
			},
		},
		"extension": []map[string]any{{
			"url":         "https://openhealthos.org/fhir/StructureDefinition/origin-facility",
			"valueString": facilityID,
		}},
	}
}

// ---------- hooks used by the HTTP handlers ----------

func (ix Integrations) onConsultFinished(c Consult) {
	if ix.shr == nil {
		return
	}
	if err := ix.shr.Put(c.PatientID, c.FacilityID, "Encounter", c.ID, ConsultToFHIREncounter(c)); err != nil {
		log.Printf("shr encounter write failed for %s (care continues; event replay catches up): %v", c.ID, err)
	}
}

// onPrescribed writes the MedicationRequest and routes to pharmacy.
// Returns whether the pharmacy accepted the route (false → pharmacy
// pulls via GET /prescriptions?status=routed when it recovers).
func (ix Integrations) onPrescribed(p Prescription, patientID, facilityID string, allergies []string) bool {
	if ix.shr != nil {
		if err := ix.shr.Put(patientID, facilityID, "MedicationRequest", p.ID,
			PrescriptionToFHIRMedicationRequest(p, patientID, facilityID)); err != nil {
			log.Printf("shr medicationrequest write failed for %s: %v", p.ID, err)
		}
	}
	if ix.pharmacy == nil {
		return false
	}
	err := ix.pharmacy.Route(PharmacyDispenseRequest{
		PrescriptionID: p.ID, PatientID: patientID, FacilityID: facilityID,
		DrugCode: p.Medication, Dose: p.Dose, Quantity: p.Quantity,
		Prescriber: p.Prescriber, Allergies: allergies,
	})
	if err != nil {
		log.Printf("pharmacy route failed for %s (pull fallback covers it): %v", p.ID, err)
		return false
	}
	return true
}
