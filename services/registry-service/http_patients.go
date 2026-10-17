package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
)

// registerPatientRoutes mounts the patient identity surface on the mux.
func registerPatientRoutes(mux *http.ServeMux, ps PatientStore) {
	mux.HandleFunc("/patients", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, 200, ps.ListPatients())
		case http.MethodPost:
			var p Patient
			if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
				http.Error(w, "bad patient: "+err.Error(), http.StatusBadRequest)
				return
			}
			p, err := ps.Register(p)
			if err != nil {
				if errors.Is(err, ErrDuplicate) {
					// Deterministic dedupe (ADR-0012): tell the registrar
					// which existing record this attempt duplicates.
					http.Error(w, err.Error(), http.StatusConflict)
					return
				}
				http.Error(w, err.Error(), http.StatusBadRequest)
				return
			}
			writeJSON(w, 201, p)
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})
	mux.HandleFunc("/patients/", func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/patients/")
		if strings.HasSuffix(path, "/nin") && r.Method == http.MethodPost {
			id := strings.TrimSuffix(path, "/nin")
			var body struct {
				NIN string `json:"nin"`
			}
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
				return
			}
			p, err := ps.LinkNIN(id, body.NIN)
			switch {
			case errors.Is(err, ErrNotFound):
				http.Error(w, "not found", http.StatusNotFound)
			case err != nil:
				http.Error(w, err.Error(), http.StatusConflict)
			default:
				writeJSON(w, 200, p)
			}
			return
		}
		p, err := ps.GetPatient(path)
		if errors.Is(err, ErrNotFound) {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		writeJSON(w, 200, p)
	})
	mux.HandleFunc("/fhir/Patient/", func(w http.ResponseWriter, r *http.Request) {
		p, err := ps.GetPatient(strings.TrimPrefix(r.URL.Path, "/fhir/Patient/"))
		if errors.Is(err, ErrNotFound) {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		writeJSON(w, 200, FHIRPatient(p))
	})
}
