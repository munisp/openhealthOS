package main

// HTTP + integrations for the IPD service (ADR-0017).
//
// Integrations:
//   - NOTIFY_URL: admission/discharge events reach the patient's phone
//     ("you have been admitted", "you have been discharged — your
//     summary is in your record");
//   - SHR_URL: discharge writes an inpatient Encounter (class IMP) and
//     a DocumentReference discharge summary, so the patient's record —
//     and the next facility they visit — sees the stay.
// Both are fire-and-log: the ADT ledger is the truth; the rails catch
// up when they can.

import (
	"bytes"
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"time"
)

type Integrations struct {
	notifyURL string
	shrURL    string
	client    *http.Client
}

func integrationsFromEnv(getenv func(string) string) Integrations {
	return Integrations{
		notifyURL: getenv("NOTIFY_URL"),
		shrURL:    getenv("SHR_URL"),
		client:    &http.Client{Timeout: 3 * time.Second},
	}
}

func (ix Integrations) fireEvent(evType string, payload map[string]any) {
	if ix.notifyURL == "" {
		return
	}
	body, _ := json.Marshal(map[string]any{
		"id": payload["event_id"], "facility": payload["facility_id"],
		"type": evType, "ts": time.Now().UTC().Format(time.RFC3339), "payload": payload,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, ix.notifyURL+"/events", bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	if resp, err := ix.client.Do(req); err == nil {
		resp.Body.Close()
	} else {
		log.Printf("notify event %s failed: %v", evType, err)
	}
}

// postDischargeSummary writes the inpatient Encounter + discharge
// summary DocumentReference into the shared health record.
func (ix Integrations) postDischargeSummary(a Admission) {
	if ix.shrURL == "" {
		return
	}
	enc := map[string]any{
		"resourceType":    "Encounter",
		"id":              "enc-" + a.ID,
		"status":          "finished",
		"class":           map[string]string{"code": "IMP", "display": "inpatient encounter"},
		"subject":         map[string]string{"reference": "Patient/" + a.PatientID},
		"period":          map[string]string{"start": a.AdmittedAt, "end": a.DischargedAt},
		"reasonCode":      []map[string]any{{"text": a.Reason}},
		"serviceProvider": map[string]string{"reference": "Organization/" + a.FacilityID},
	}
	doc := map[string]any{
		"resourceType": "DocumentReference",
		"id":           "doc-" + a.ID,
		"status":       "current",
		"type":         map[string]any{"text": "Discharge summary"},
		"description":  "Discharge summary — " + a.ID,
		"subject":      map[string]string{"reference": "Patient/" + a.PatientID},
		"date":         a.DischargedAt,
		"content": []map[string]any{{
			"attachment": map[string]any{"contentType": "text/plain", "title": a.Summary},
		}},
	}
	for _, pair := range [][2]any{{"Encounter", enc}, {"DocumentReference", doc}} {
		res, _ := pair[1].(map[string]any)
		body, _ := json.Marshal(res)
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		req, err := http.NewRequestWithContext(ctx, http.MethodPut,
			ix.shrURL+"/fhir/"+pair[0].(string)+"/"+res["id"].(string), bytes.NewReader(body))
		if err != nil {
			cancel()
			continue
		}
		req.Header.Set("Content-Type", "application/fhir+json")
		resp, err := ix.client.Do(req)
		cancel()
		if err != nil {
			log.Printf("shr discharge write %s failed: %v", res["id"], err)
			continue
		}
		resp.Body.Close()
		if resp.StatusCode >= 300 {
			log.Printf("shr discharge write %s: status %d", res["id"], resp.StatusCode)
		}
	}
}

type server struct {
	store Store
	ix    Integrations
	now   func() time.Time
}

type idGen interface{ NextID() string }

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()

	mux.HandleFunc("POST /wards", func(w http.ResponseWriter, r *http.Request) {
		var ward Ward
		if !decode(w, r, &ward) {
			return
		}
		if err := s.store.PutWard(ward); err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, 201, ward)
	})
	mux.HandleFunc("GET /wards", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"wards": s.store.Wards(r.URL.Query().Get("facility_id"))})
	})
	mux.HandleFunc("POST /beds", func(w http.ResponseWriter, r *http.Request) {
		var bed Bed
		if !decode(w, r, &bed) {
			return
		}
		if bed.ID == "" || bed.WardID == "" {
			http.Error(w, `{"error":"id and ward_id required"}`, http.StatusBadRequest)
			return
		}
		if err := s.store.PutBed(bed); err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, 201, bed)
	})
	mux.HandleFunc("GET /beds", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"beds": s.store.Beds(r.URL.Query().Get("ward_id"))})
	})
	mux.HandleFunc("POST /beds/{id}/state", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			State string `json:"state"`
		}
		if !decode(w, r, &req) || (req.State != BedFree && req.State != BedCleaning) {
			http.Error(w, `{"error":"state must be free or cleaning"}`, http.StatusBadRequest)
			return
		}
		if err := s.store.SetBedState(r.PathValue("id"), req.State); err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, 200, map[string]string{"id": r.PathValue("id"), "state": req.State})
	})

	mux.HandleFunc("POST /admissions", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID  string `json:"patient_id"`
			FacilityID string `json:"facility_id"`
			WardID     string `json:"ward_id"`
			BedID      string `json:"bed_id"`
			Reason     string `json:"reason"`
		}
		if !decode(w, r, &req) || req.PatientID == "" || req.WardID == "" || req.BedID == "" {
			http.Error(w, `{"error":"patient_id, ward_id and bed_id required"}`, http.StatusBadRequest)
			return
		}
		id := "ADM-LOCAL"
		if g, ok := s.store.(idGen); ok {
			id = g.NextID()
		}
		a := Admission{
			ID: id, PatientID: req.PatientID, FacilityID: req.FacilityID,
			WardID: req.WardID, BedID: req.BedID, Reason: req.Reason,
			Status: AdmOpen, AdmittedAt: s.now().UTC().Format(time.RFC3339),
		}
		if err := s.store.Admit(a); err != nil {
			fail(w, err)
			return
		}
		go s.ix.fireEvent("admission", map[string]any{
			"event_id": "ipd-" + a.ID, "facility_id": a.FacilityID,
			"patient_id": a.PatientID, "facility_name": a.FacilityID,
		})
		writeJSON(w, 201, a)
	})
	mux.HandleFunc("GET /admissions", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		if pid := q.Get("patient_id"); pid != "" {
			a, err := s.store.OpenAdmission(pid)
			if err != nil {
				writeJSON(w, 200, map[string]any{"admission": nil})
				return
			}
			writeJSON(w, 200, map[string]any{"admission": a})
			return
		}
		writeJSON(w, 200, map[string]any{
			"admissions": s.store.Admissions(q.Get("facility_id"), q.Get("status")),
		})
	})
	mux.HandleFunc("GET /admissions/{id}", func(w http.ResponseWriter, r *http.Request) {
		a, err := s.store.Admission(r.PathValue("id"))
		if err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, 200, a)
	})
	mux.HandleFunc("POST /admissions/{id}/transfer", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			ToBedID string `json:"to_bed_id"`
		}
		if !decode(w, r, &req) || req.ToBedID == "" {
			http.Error(w, `{"error":"to_bed_id required"}`, http.StatusBadRequest)
			return
		}
		a, err := s.store.Transfer(r.PathValue("id"), req.ToBedID, s.now().UTC().Format(time.RFC3339))
		if err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, 200, a)
	})
	mux.HandleFunc("POST /admissions/{id}/discharge", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Summary string `json:"summary"`
		}
		if !decode(w, r, &req) {
			return
		}
		a, err := s.store.Discharge(r.PathValue("id"), s.now().UTC().Format(time.RFC3339), req.Summary)
		if err != nil {
			fail(w, err)
			return
		}
		go s.ix.postDischargeSummary(a)
		go s.ix.fireEvent("discharge", map[string]any{
			"event_id": "ipd-dc-" + a.ID, "facility_id": a.FacilityID,
			"patient_id": a.PatientID, "facility_name": a.FacilityID,
		})
		writeJSON(w, 200, a)
	})

	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte("ok"))
	})
	return mux
}

func decode(w http.ResponseWriter, r *http.Request, v any) bool {
	if err := json.NewDecoder(r.Body).Decode(v); err != nil {
		http.Error(w, `{"error":"bad json: `+err.Error()+`"}`, http.StatusBadRequest)
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, err error) {
	switch err {
	case ErrNotFound:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	case ErrBedOccupied, ErrAlreadyAdmitted, ErrNotAdmitted:
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusConflict)
	default:
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
	}
}

func main() {
	addr := ":8093"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	var store Store = NewMemoryStore()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		pg, err := NewPostgresStore(dsn)
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		store = pg
		log.Print("ipd storage: postgres (state plane)")
	} else {
		log.Print("ipd storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{store: store, ix: integrationsFromEnv(os.Getenv), now: time.Now}
	log.Printf("OpenHealth OS ipd service on %s (wards, beds, ADT)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
