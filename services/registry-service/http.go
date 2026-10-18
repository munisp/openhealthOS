package main

import (
	"encoding/json"
	"errors"
	"fmt"
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
	mux.HandleFunc("/facilities/near", s.facilitiesNear) // exact beats subtree
	mux.HandleFunc("/facilities/search", s.facilitiesSearch)
	mux.HandleFunc("/providers/search", s.providersSearch) // exact beats subtree
	mux.HandleFunc("/facilities/", s.facility)
	mux.HandleFunc("/providers", s.providers)
	mux.HandleFunc("/providers/", s.provider)
	mux.HandleFunc("/fhir/Location/", s.fhirLocation)
	mux.HandleFunc("/fhir/Practitioner/", s.fhirPractitioner)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}

// facilitiesNear answers "what's around me" (ADR-0014):
// GET /facilities/near?lat=&lng=&radius_km=&level=&amenity=&limit=
func (s server) facilitiesNear(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	lat, lng, radius, limit, ok := parseGeoParams(r.URL.Query())
	if !ok {
		http.Error(w, `{"error":"valid lat and lng are required (radius_km<=500, limit<=500)"}`, http.StatusBadRequest)
		return
	}
	writeJSON(w, 200, map[string]any{
		"query":   map[string]any{"lat": lat, "lng": lng, "radius_km": radius},
		"results": NearbyFacilities(s.store.ListFacilities(), lat, lng, radius, r.URL.Query().Get("level"), r.URL.Query().Get("amenity"), limit),
	})
}

// facilitiesSearch (ADR-0015): GET /facilities/search?q=&limit=
// AND-prefix full-text, ranked — "mai" finds "Maiduguri Baga Road PHC".
func (s server) facilitiesSearch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	q, limit, ok := parseSearchParams(w, r)
	if !ok {
		return
	}
	writeJSON(w, 200, map[string]any{
		"query":   q,
		"results": s.store.SearchFacilities(q, limit),
	})
}

// parseSearchParams enforces the search contract shared by both search
// endpoints: q >= 2 chars (a one-character query is a full scan in
// disguise), limit capped at 100.
func parseSearchParams(w http.ResponseWriter, r *http.Request) (string, int, bool) {
	q := strings.TrimSpace(r.URL.Query().Get("q"))
	if len([]rune(q)) < 2 {
		http.Error(w, `{"error":"q must be at least 2 characters"}`, http.StatusBadRequest)
		return "", 0, false
	}
	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		var n int
		if _, err := fmt.Sscanf(v, "%d", &n); err == nil && n > 0 {
			limit = n
		}
	}
	if limit > 100 {
		limit = 100
	}
	return q, limit, true
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

// providersSearch (ADR-0017): GET /providers/search?q=&limit=
func (s server) providersSearch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	q := r.URL.Query().Get("q")
	limit := 20
	writeJSON(w, 200, map[string]any{"query": q, "results": s.store.SearchProviders(q, limit)})
}
