package main

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
)

// server exposes the registry over HTTP (OpenHIE-style REST surface).
type server struct {
	store Store
}

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/facilities", s.facilities)
	mux.HandleFunc("/facilities/", s.facility)
	mux.HandleFunc("/providers", s.providers)
	mux.HandleFunc("/providers/", s.provider)
	mux.HandleFunc("/fhir/Location/", s.fhirLocation)
	mux.HandleFunc("/fhir/Practitioner/", s.fhirPractitioner)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s server) facilities(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, 200, s.store.ListFacilities())
	case http.MethodPost:
		var f Facility
		if err := json.NewDecoder(r.Body).Decode(&f); err != nil {
			http.Error(w, "bad facility: "+err.Error(), http.StatusBadRequest)
			return
		}
		if err := s.store.PutFacility(f); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, 201, f)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s server) facility(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	f, err := s.store.GetFacility(strings.TrimPrefix(r.URL.Path, "/facilities/"))
	if errors.Is(err, ErrNotFound) {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	writeJSON(w, 200, f)
}

func (s server) providers(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, 200, s.store.ListProviders())
	case http.MethodPost:
		var p Provider
		if err := json.NewDecoder(r.Body).Decode(&p); err != nil {
			http.Error(w, "bad provider: "+err.Error(), http.StatusBadRequest)
			return
		}
		if err := s.store.PutProvider(p); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, 201, p)
	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s server) provider(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	p, err := s.store.GetProvider(strings.TrimPrefix(r.URL.Path, "/providers/"))
	if errors.Is(err, ErrNotFound) {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	writeJSON(w, 200, p)
}

func (s server) fhirLocation(w http.ResponseWriter, r *http.Request) {
	f, err := s.store.GetFacility(strings.TrimPrefix(r.URL.Path, "/fhir/Location/"))
	if errors.Is(err, ErrNotFound) {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	writeJSON(w, 200, FHIRLocation(f))
}

func (s server) fhirPractitioner(w http.ResponseWriter, r *http.Request) {
	p, err := s.store.GetProvider(strings.TrimPrefix(r.URL.Path, "/fhir/Practitioner/"))
	if errors.Is(err, ErrNotFound) {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	writeJSON(w, 200, FHIRPractitioner(p))
}
