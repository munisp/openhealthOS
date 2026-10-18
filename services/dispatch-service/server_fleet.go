package main

// HTTP surface for the fleet + the live track endpoint (ADR-0021):
// units register and ping, dispatch assigns, and /incidents/{id}/track
// answers "where is help right now, and when will it reach me" — the
// payload the uber-like map in the PWA and patient app renders.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// Incident needs a unit link — add it to the struct in main.go:
// UnitID string `json:"unit_id,omitempty"`.

func (s server) fleetRoutes(mux *http.ServeMux) {
	mux.HandleFunc("POST /units", s.registerUnit)
	mux.HandleFunc("GET /units", s.listUnits)
	mux.HandleFunc("GET /units/{id}", s.getUnit)
	mux.HandleFunc("POST /units/{id}/position", s.pingUnit)
	mux.HandleFunc("POST /units/{id}/status", s.unitStatus)
	mux.HandleFunc("POST /incidents/{id}/dispatch-unit", s.dispatchUnit)
	mux.HandleFunc("GET /incidents/{id}/track", s.track)
}

func (s server) registerUnit(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Callsign   string `json:"callsign"`
		Kind       string `json:"kind"`
		FacilityID string `json:"facility_id"`
		Crew       string `json:"crew"`
		Phone      string `json:"phone"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fail(w, fmt.Errorf("%w: bad json", ErrValidation))
		return
	}
	u, err := s.fleet.Register(req.Callsign, req.Kind, req.FacilityID, req.Crew, req.Phone, s.ix.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 201, u)
}

func (s server) listUnits(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{"units": s.fleet.List(r.URL.Query().Get("status"))})
}

func (s server) getUnit(w http.ResponseWriter, r *http.Request) {
	u, err := s.fleet.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, u)
}

// pingUnit is the paramedic device's GPS heartbeat.
func (s server) pingUnit(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Lat float64 `json:"lat"`
		Lng float64 `json:"lng"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		fail(w, fmt.Errorf("%w: bad json", ErrValidation))
		return
	}
	u, err := s.fleet.Ping(r.PathValue("id"), req.Lat, req.Lng, s.ix.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, u)
}

func (s server) unitStatus(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Status == "" {
		fail(w, fmt.Errorf("%w: status required", ErrValidation))
		return
	}
	u, err := s.fleet.SetStatus(r.PathValue("id"), req.Status, "", s.ix.now())
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, u)
}

// dispatchUnit assigns the nearest available unit — or a named one
// when the dispatcher overrides (manual beats algorithm, timeline says who).
func (s server) dispatchUnit(w http.ResponseWriter, r *http.Request) {
	var req struct {
		UnitID string `json:"unit_id"`
		Actor  string `json:"actor"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Actor == "" {
		fail(w, fmt.Errorf("%w: actor required", ErrValidation))
		return
	}
	inc, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	if inc.Status == StClosed || inc.Status == StCancelled {
		fail(w, ErrBadTransition)
		return
	}
	var u Unit
	var km float64
	if req.UnitID != "" {
		u, err = s.fleet.Get(req.UnitID)
		if err != nil {
			fail(w, err)
			return
		}
		km = HaversineKm(inc.Lat, inc.Lng, u.Lat, u.Lng)
	} else {
		u, km, err = s.fleet.NearestAvailable(inc.Lat, inc.Lng)
		if err != nil {
			fail(w, fmt.Errorf("%w: %s", ErrValidation, err.Error()))
			return
		}
	}
	if _, err := s.fleet.SetStatus(u.ID, UnitEnRoute, inc.ID, s.ix.now()); err != nil {
		fail(w, err)
		return
	}
	inc.UnitID = u.ID
	inc.UpdatedAt = s.ix.now().UTC().Format(time.RFC3339)
	inc.Timeline = append(inc.Timeline, TimelineEntry{
		TS: inc.UpdatedAt, Actor: req.Actor, Action: "unit-dispatched",
		Detail: fmt.Sprintf("%s (%s, %.1fkm)", u.Callsign, u.ID, km),
	})
	_ = s.store.Put(inc)
	go s.ix.fireEvent("dispatch_update", map[string]any{
		"event_id": "unit-" + inc.ID, "facility_id": inc.FacilityID,
		"caller_phone": inc.CallerPhone, "incident_id": inc.ID,
		"status": "unit_en_route", "unit_callsign": u.Callsign,
	})
	writeJSON(w, 200, inc)
}

// track answers the map's question: patient, facility, unit — and an
// honest ETA with its assumptions printed.
func (s server) track(w http.ResponseWriter, r *http.Request) {
	inc, err := s.store.Get(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	resp := map[string]any{
		"incident_id": inc.ID, "status": inc.Status, "triage": inc.Triage,
		"patient": map[string]float64{"lat": inc.Lat, "lng": inc.Lng},
		"as_of":   s.ix.now().UTC().Format(time.RFC3339),
	}
	if inc.FacilityID != "" {
		resp["facility"] = map[string]any{"id": inc.FacilityID, "name": inc.FacilityName}
	}
	if inc.UnitID != "" {
		if u, err := s.fleet.Get(inc.UnitID); err == nil {
			dist := HaversineKm(inc.Lat, inc.Lng, u.Lat, u.Lng)
			mins, how := ETA(dist)
			resp["unit"] = map[string]any{
				"id": u.ID, "callsign": u.Callsign, "kind": u.Kind,
				"status": u.Status, "lat": u.Lat, "lng": u.Lng,
				"trail": u.Trail, "updated_at": u.UpdatedAt,
			}
			resp["eta"] = map[string]any{
				"minutes": mins, "distance_km": dist, "assumptions": how,
			}
		}
	}
	writeJSON(w, 200, resp)
}
