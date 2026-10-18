// portal_actions.go — the patient's self-service rail (ADR-0017):
// booking appointments, checking coverage, asking the clinic, and
// managing record-sharing consents — all from the phone, all behind
// the same subscription + phone gate as /portal/record (404 on any
// mismatch; patient ids are never enumerable).
//
// Every handler is a thin, honest proxy: the portal is the trust
// boundary (it knows the patient's phone), the domain services stay
// the system of record. Timeouts are short and failures return 502
// with a plain error — the app renders its cached copy or a retry
// affordance, never a spinner forever.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"time"
)

var (
	schedulingBase  = func() string { return envOr("SCHEDULING_URL", "http://127.0.0.1:8092") }
	eligibilityBase = func() string { return envOr("ELIGIBILITY_URL", "http://127.0.0.1:8080") }
	consentBase     = func() string { return envOr("CONSENT_URL", "http://127.0.0.1:8082") }
	registryBase    = func() string { return envOr("REGISTRY_URL", "http://127.0.0.1:8081") }
	// portalClient is a var so tests can stub transports.
	portalClient = &http.Client{Timeout: 4 * time.Second}
)

// portalGate authenticates the portal pair (patient id + phone on file).
// The 404 is deliberately indistinguishable between "no such patient"
// and "wrong phone".
func portalGate(svc *Service, w http.ResponseWriter, patientID, phone string) bool {
	pid := patientID
	ph := NormalizePhone(phone)
	sub, err := svc.store.Subscription(pid)
	if err != nil || sub.Phone != ph || pid == "" || ph == "" {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return false
	}
	return true
}

// proxyJSON forwards one JSON request upstream and streams the answer
// back, preserving the upstream status code. ok=false on transport
// failure → 502.
func proxyJSON(ctx context.Context, w http.ResponseWriter, method, url string, body any) {
	var rdr io.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, rdr)
	if err != nil {
		http.Error(w, `{"error":"upstream unavailable"}`, http.StatusBadGateway)
		return
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := portalClient.Do(req)
	if err != nil {
		http.Error(w, `{"error":"upstream unavailable"}`, http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	io.Copy(w, io.LimitReader(resp.Body, 1<<20))
}

func registerPortalActions(mux *http.ServeMux, svc *Service) {
	// ---- coverage: "am I enrolled, and until when?" ----
	mux.HandleFunc("GET /portal/coverage", func(w http.ResponseWriter, r *http.Request) {
		pid := r.URL.Query().Get("patient_id")
		if !portalGate(svc, w, pid, r.URL.Query().Get("phone")) {
			return
		}
		proxyJSON(r.Context(), w, http.MethodPost, eligibilityBase()+"/eligibility", map[string]string{
			"member_id":    pid,
			"service_date": time.Now().UTC().Format("2006-01-02"),
		})
	})

	// ---- facilities: "where can I book?" — the registry's public list ----
	mux.HandleFunc("GET /portal/facilities", func(w http.ResponseWriter, r *http.Request) {
		pid := r.URL.Query().Get("patient_id")
		if !portalGate(svc, w, pid, r.URL.Query().Get("phone")) {
			return
		}
		proxyJSON(r.Context(), w, http.MethodGet, registryBase()+"/facilities", nil)
	})

	// ---- appointments: browse slots, book, list mine, cancel ----
	mux.HandleFunc("GET /portal/slots", func(w http.ResponseWriter, r *http.Request) {
		pid := r.URL.Query().Get("patient_id")
		if !portalGate(svc, w, pid, r.URL.Query().Get("phone")) {
			return
		}
		q := r.URL.Query()
		proxyJSON(r.Context(), w, http.MethodGet, schedulingBase()+"/slots?facility_id="+
			q.Get("facility_id")+"&service="+q.Get("service")+"&date="+q.Get("date"), nil)
	})

	mux.HandleFunc("GET /portal/appointments", func(w http.ResponseWriter, r *http.Request) {
		pid := r.URL.Query().Get("patient_id")
		if !portalGate(svc, w, pid, r.URL.Query().Get("phone")) {
			return
		}
		proxyJSON(r.Context(), w, http.MethodGet, schedulingBase()+"/appointments?patient_id="+pid, nil)
	})

	mux.HandleFunc("POST /portal/appointments", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
			Phone     string `json:"phone"`
			SlotID    string `json:"slot_id"`
			MemberID  string `json:"member_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.SlotID == "" {
			http.Error(w, `{"error":"slot_id required"}`, http.StatusBadRequest)
			return
		}
		if !portalGate(svc, w, req.PatientID, req.Phone) {
			return
		}
		memberID := req.MemberID
		if memberID == "" {
			memberID = req.PatientID // enrolment ids track patient ids by default
		}
		proxyJSON(r.Context(), w, http.MethodPost, schedulingBase()+"/appointments", map[string]string{
			"slot_id": req.SlotID, "patient_id": req.PatientID, "member_id": memberID,
		})
	})

	mux.HandleFunc("POST /portal/appointments/cancel", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID     string `json:"patient_id"`
			Phone         string `json:"phone"`
			AppointmentID string `json:"appointment_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.AppointmentID == "" {
			http.Error(w, `{"error":"appointment_id required"}`, http.StatusBadRequest)
			return
		}
		if !portalGate(svc, w, req.PatientID, req.Phone) {
			return
		}
		proxyJSON(r.Context(), w, http.MethodPost,
			schedulingBase()+"/appointments/"+req.AppointmentID+"/cancel", map[string]string{})
	})

	// ---- ask the clinic: opens a triaged telehealth consult ----
	mux.HandleFunc("POST /portal/messages", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID  string `json:"patient_id"`
			Phone      string `json:"phone"`
			FacilityID string `json:"facility_id"`
			Complaint  string `json:"complaint"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Complaint == "" {
			http.Error(w, `{"error":"complaint required"}`, http.StatusBadRequest)
			return
		}
		if !portalGate(svc, w, req.PatientID, req.Phone) {
			return
		}
		// The consult opens under the portal's triage identity — the
		// facility's nurse pool picks it up from the inbox.
		proxyJSON(r.Context(), w, http.MethodPost, telehealthBase()+"/consults", map[string]string{
			"patient_id": req.PatientID, "facility_id": req.FacilityID,
			"nurse_id": "portal-triage", "complaint": req.Complaint,
		})
	})

	// ---- consents: "who can see my record" + revoke ----
	mux.HandleFunc("GET /portal/consents", func(w http.ResponseWriter, r *http.Request) {
		pid := r.URL.Query().Get("patient_id")
		if !portalGate(svc, w, pid, r.URL.Query().Get("phone")) {
			return
		}
		proxyJSON(r.Context(), w, http.MethodGet, consentBase()+"/consents?patient_id="+pid, nil)
	})

	mux.HandleFunc("POST /portal/consents/revoke", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			PatientID string `json:"patient_id"`
			Phone     string `json:"phone"`
			ConsentID string `json:"consent_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.ConsentID == "" {
			http.Error(w, `{"error":"consent_id required"}`, http.StatusBadRequest)
			return
		}
		if !portalGate(svc, w, req.PatientID, req.Phone) {
			return
		}
		// Ownership check: a patient revokes only their own artefacts.
		if !consentOwnedBy(r.Context(), req.ConsentID, req.PatientID) {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
			return
		}
		proxyJSON(r.Context(), w, http.MethodPost,
			consentBase()+"/consents/"+req.ConsentID+"/revoke", map[string]string{})
	})
}

// consentOwnedBy fetches the patient's consents and answers whether id
// is among them — the portal never lets one patient act on another's
// consent artefact.
func consentOwnedBy(ctx context.Context, consentID, patientID string) bool {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		consentBase()+"/consents?patient_id="+patientID, nil)
	if err != nil {
		return false
	}
	resp, err := portalClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()
	var body struct {
		Consents []struct {
			ID string `json:"id"`
		} `json:"consents"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return false
	}
	for _, c := range body.Consents {
		if c.ID == consentID {
			return true
		}
	}
	return false
}
