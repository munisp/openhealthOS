// HTTP surface for the pharmacy service.
package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"os"
	"sort"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"
)

type server struct {
	svc *Service
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func fail(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, ErrNotFound):
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	case errors.Is(err, ErrUnknownDrug), errors.Is(err, ErrBadQuantity):
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
	case errors.Is(err, ErrOutOfStock), errors.Is(err, ErrAllergyBlock):
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusConflict)
	case errors.Is(err, ErrBadTransition):
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusConflict)
	default:
		http.Error(w, `{"error":"internal"}`, http.StatusInternalServerError)
	}
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()

	mux.HandleFunc("GET /formulary", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, Formulary())
	})

	mux.HandleFunc("GET /stock", func(w http.ResponseWriter, r *http.Request) {
		facility := r.URL.Query().Get("facility")
		if facility == "" {
			http.Error(w, `{"error":"facility required"}`, http.StatusBadRequest)
			return
		}
		stock := s.svc.store.Stock(facility)
		sort.Slice(stock, func(i, j int) bool { return stock[i].DrugCode < stock[j].DrugCode })
		writeJSON(w, http.StatusOK, stock)
	})

	mux.HandleFunc("POST /stock/receive", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			FacilityID   string `json:"facility_id"`
			DrugCode     string `json:"drug_code"`
			Quantity     int    `json:"quantity"`
			ReorderLevel int    `json:"reorder_level"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.FacilityID == "" || body.DrugCode == "" {
			http.Error(w, `{"error":"facility_id and drug_code required"}`, http.StatusBadRequest)
			return
		}
		lvl, err := s.svc.ReceiveStock(body.FacilityID, body.DrugCode, body.Quantity, body.ReorderLevel)
		if err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, http.StatusOK, lvl)
	})

	// Telehealth routes prescriptions here.
	mux.HandleFunc("POST /dispense-requests", func(w http.ResponseWriter, r *http.Request) {
		var req DispenseRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil ||
			req.PrescriptionID == "" || req.PatientID == "" || req.FacilityID == "" || req.DrugCode == "" {
			http.Error(w, `{"error":"prescription_id, patient_id, facility_id, drug_code required"}`, http.StatusBadRequest)
			return
		}
		out, err := s.svc.RoutePrescription(req)
		if err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, http.StatusCreated, out)
	})

	mux.HandleFunc("GET /dispense-requests", func(w http.ResponseWriter, r *http.Request) {
		out := s.svc.store.Requests(r.URL.Query().Get("facility"), r.URL.Query().Get("status"))
		sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
		writeJSON(w, http.StatusOK, out)
	})

	mux.HandleFunc("POST /dispense-requests/{id}/dispense", func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			StaffID string `json:"staff_id"`
		}
		json.NewDecoder(r.Body).Decode(&body)
		if body.StaffID == "" {
			body.StaffID = "unknown"
		}
		req, draft, err := s.svc.Dispense(r.PathValue("id"), body.StaffID)
		if err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"request": req, "claim_draft": draft})
	})

	mux.HandleFunc("GET /claim-drafts", func(w http.ResponseWriter, r *http.Request) {
		out := s.svc.store.Drafts(r.URL.Query().Get("facility"), r.URL.Query().Get("status"))
		sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
		writeJSON(w, http.StatusOK, out)
	})

	mux.HandleFunc("POST /claim-drafts/{id}/submit", func(w http.ResponseWriter, r *http.Request) {
		if err := s.svc.store.MarkDraftSubmitted(r.PathValue("id")); err != nil {
			fail(w, err)
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"status": "submitted"})
	})

	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

func main() {
	var store Store = NewMemoryStore()
	if os.Getenv("DATABASE_URL") != "" {
		pg, err := NewPostgresStore(os.Getenv("DATABASE_URL"))
		if err != nil {
			panic(err)
		}
		store = pg
	}
	addr := ":8088"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	var handler http.Handler = server{svc: NewService(store)}.routes()
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		panic(authErr)
	}
	if authV != nil {
		handler = authV.Middleware(handler)
	}
	log := func(msg string) { println("pharmacy-service:", msg) }
	log("listening on " + addr)
	if err := servex.InstrumentedListenAndServe("pharmacy-service", os.Getenv, addr, handler); err != nil {
		panic(err)
	}
}
