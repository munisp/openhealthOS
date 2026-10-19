// portal_record.go — the patient's own record on their own phone
// (ADR-0016).
//
// The portal gate already authenticates patient code + phone against the
// subscription store (404 on any mismatch — no enumeration). This handler
// fans out to the clinical spine on the patient's behalf:
//
//	SHR  /fhir/Patient/{id}/$everything  (X-Patient-Self — self-access
//	                                     basis, audited as portal:<id>)
//	telehealth /consults?patient_id=     (the consult thread)
//
// Degradation doctrine: a dark SHR or telehealth node must never 500 the
// patient's phone. Each leg has its own short timeout; a failed leg comes
// back as an explicit null with a completeness flag, and the app renders
// its last cached copy with an honest banner instead.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"time"
)

// PortalRecord is the merged portal payload.
type PortalRecord struct {
	PatientID   string          `json:"patient_id"`
	GeneratedAt string          `json:"generated_at"`
	Complete    bool            `json:"complete"` // false when a leg was unreachable
	Record      json.RawMessage `json:"record"`   // FHIR Bundle (searchset) or null
	Consults    json.RawMessage `json:"consults"` // telehealth thread array or null
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

var (
	shrBase        = func() string { return envOr("SHR_URL", "http://127.0.0.1:8084") }
	telehealthBase = func() string { return envOr("TELEHEALTH_URL", "http://127.0.0.1:8086") }
	// recordClient is a var so tests can stub transport.
	recordClient = &http.Client{Timeout: 4 * time.Second}
)

// emptyBundle is the truthful shape of "no record yet" for the SHR leg.
var emptyBundle = json.RawMessage(`{"resourceType":"Bundle","type":"searchset","total":0,"entry":[]}`)

// fetchPortalLeg GETs one upstream; ok=false marks the record incomplete.
// A 404 is not an outage — the caller's notFound fallback is the truth
// ("this patient has nothing here yet").
func fetchPortalLeg(ctx context.Context, url string, headers map[string]string, notFound json.RawMessage) (json.RawMessage, bool) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, false
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := recordClient.Do(req)
	if err != nil {
		return nil, false
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return notFound, true
	}
	if resp.StatusCode != http.StatusOK {
		return nil, false
	}
	var body json.RawMessage
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return nil, false
	}
	return body, true
}

// portalRecord builds the merged record for an authenticated patient.
func portalRecord(ctx context.Context, patientID string) PortalRecord {
	out := PortalRecord{
		PatientID:   patientID,
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Complete:    true,
	}
	bundle, ok := fetchPortalLeg(ctx,
		fmt.Sprintf("%s/fhir/Patient/%s/$everything", shrBase(), patientID),
		map[string]string{"X-Patient-Self": patientID}, emptyBundle)
	if !ok {
		out.Complete = false
	} else {
		out.Record = bundle
	}
	raw, ok := fetchPortalLeg(ctx,
		fmt.Sprintf("%s/consults?patient_id=%s", telehealthBase(), patientID), nil,
		json.RawMessage(`{"consults":[]}`))
	if !ok {
		out.Complete = false
	} else {
		// Unwrap {"consults": [...]} so the app gets a bare array.
		var wrap struct {
			Consults json.RawMessage `json:"consults"`
		}
		if err := json.Unmarshal(raw, &wrap); err == nil &&
			len(wrap.Consults) > 0 && string(wrap.Consults) != "null" {
			out.Consults = wrap.Consults
		} else {
			out.Consults = json.RawMessage(`[]`)
		}
	}
	return out
}

// registerPortalRecord wires GET /portal/record behind the step-up gate:
// the subscription pair authenticates the session, but RENDERING the
// record (clinical detail — PHI) additionally requires an elevated token
// that survives the random re-auth roll (stepup.go).
func registerPortalRecord(mux *http.ServeMux, srv server) {
	mux.HandleFunc("GET /portal/record", func(w http.ResponseWriter, r *http.Request) {
		pid, ok := srv.requireElevation(w, r)
		if !ok {
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(portalRecord(r.Context(), pid))
	})
}
