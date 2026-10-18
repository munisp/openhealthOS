package main

// fleet.go — ambulance units and live positions (ADR-0021).
//
// Dispatch knew where the patient is and where the facility is; it
// was blind on the third point of the triangle — the ambulance. This
// file adds the fleet: units register once, their devices ping GPS
// positions en route, and anyone holding the incident link can watch
// help approach on a map, the way you watch a ride arrive.
//
// Design rules:
//   - positions are ephemeral hot state: the latest fix matters, the
//     trail is a short ring for the map — fleet state lives in memory
//     by design, rebuilt from device pings after any restart;
//   - ETA is honest arithmetic: haversine x road factor / speed, with
//     the assumptions printed on the response — never a fake-precise
//     countdown;
//   - unit status rides the incident lifecycle: dispatch assigns the
//     nearest AVAILABLE unit, incident transitions move it
//     (on_scene, transporting, back to available on closure).

import (
	"fmt"
	"math"
	"sort"
	"sync"
	"time"
)

// Unit kinds and statuses.
const (
	UnitAmbulance = "ambulance"
	UnitMotorbike = "motorbike" // rural first-responder
	UnitBoat      = "boat"      // riverine LGAs

	UnitAvailable    = "available"
	UnitEnRoute      = "en_route"
	UnitOnScene      = "on_scene"
	UnitTransporting = "transporting"
	UnitOffDuty      = "off_duty"
)

// ETA assumptions, printed on every track response.
const (
	RoadFactor    = 1.4  // straight-line -> road distance multiplier
	UrbanSpeedKmh = 40.0 // conservative urban ambulance average
)

type Position struct {
	Lat float64 `json:"lat"`
	Lng float64 `json:"lng"`
	TS  string  `json:"ts"`
}

type Unit struct {
	ID         string     `json:"id"`
	Callsign   string     `json:"callsign"`
	Kind       string     `json:"kind"`
	FacilityID string     `json:"facility_id"`
	Crew       string     `json:"crew,omitempty"`
	Phone      string     `json:"phone,omitempty"`
	Status     string     `json:"status"`
	Lat        float64    `json:"lat"`
	Lng        float64    `json:"lng"`
	UpdatedAt  string     `json:"updated_at"`
	Trail      []Position `json:"trail,omitempty"` // recent fixes, newest last
	IncidentID string     `json:"incident_id,omitempty"`
}

// trailKeep is how many fixes the map trail holds.
const trailKeep = 50

type FleetStore struct {
	mu    sync.Mutex
	units map[string]Unit
	seq   int
}

func NewFleetStore() *FleetStore {
	return &FleetStore{units: map[string]Unit{}}
}

func (f *FleetStore) Register(callsign, kind, facilityID, crew, phone string, now time.Time) (Unit, error) {
	if callsign == "" || facilityID == "" {
		return Unit{}, fmt.Errorf("%w: callsign and facility_id required", ErrValidation)
	}
	if kind == "" {
		kind = UnitAmbulance
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.seq++
	u := Unit{
		ID: fmt.Sprintf("UNIT-%04d", f.seq), Callsign: callsign, Kind: kind,
		FacilityID: facilityID, Crew: crew, Phone: phone,
		Status: UnitAvailable, UpdatedAt: now.UTC().Format(time.RFC3339),
	}
	f.units[u.ID] = u
	return u, nil
}

// Ping records a GPS fix from the unit's device.
func (f *FleetStore) Ping(id string, lat, lng float64, now time.Time) (Unit, error) {
	if lat == 0 && lng == 0 {
		return Unit{}, fmt.Errorf("%w: coordinates required", ErrValidation)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	u, ok := f.units[id]
	if !ok {
		return Unit{}, ErrNotFound
	}
	ts := now.UTC().Format(time.RFC3339)
	u.Lat, u.Lng, u.UpdatedAt = lat, lng, ts
	u.Trail = append(u.Trail, Position{Lat: lat, Lng: lng, TS: ts})
	if len(u.Trail) > trailKeep {
		u.Trail = u.Trail[len(u.Trail)-trailKeep:]
	}
	f.units[id] = u
	return u, nil
}

func (f *FleetStore) SetStatus(id, status, incidentID string, now time.Time) (Unit, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	u, ok := f.units[id]
	if !ok {
		return Unit{}, ErrNotFound
	}
	u.Status = status
	u.IncidentID = incidentID
	u.UpdatedAt = now.UTC().Format(time.RFC3339)
	f.units[id] = u
	return u, nil
}

func (f *FleetStore) Get(id string) (Unit, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if u, ok := f.units[id]; ok {
		return u, nil
	}
	return Unit{}, ErrNotFound
}

func (f *FleetStore) List(status string) []Unit {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := []Unit{}
	for _, u := range f.units {
		if status != "" && u.Status != status {
			continue
		}
		out = append(out, u)
	}
	sort.Slice(out, func(a, b int) bool { return out[a].ID < out[b].ID })
	return out
}

// NearestAvailable finds the closest available unit with a fix —
// the unit dispatched when an incident goes out.
func (f *FleetStore) NearestAvailable(lat, lng float64) (Unit, float64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	best, bestKm := Unit{}, math.MaxFloat64
	for _, u := range f.units {
		if u.Status != UnitAvailable || (u.Lat == 0 && u.Lng == 0) {
			continue
		}
		if d := HaversineKm(lat, lng, u.Lat, u.Lng); d < bestKm {
			best, bestKm = u, d
		}
	}
	if best.ID == "" {
		return Unit{}, 0, fmt.Errorf("no available unit with a position fix")
	}
	return best, bestKm, nil
}

// HaversineKm mirrors the registry's geo.go — great-circle distance.
func HaversineKm(lat1, lng1, lat2, lng2 float64) float64 {
	const R = 6371.0
	rad := math.Pi / 180
	dLat := (lat2 - lat1) * rad
	dLng := (lng2 - lng1) * rad
	a := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(lat1*rad)*math.Cos(lat2*rad)*math.Sin(dLng/2)*math.Sin(dLng/2)
	return R * 2 * math.Atan2(math.Sqrt(a), math.Sqrt(1-a))
}

// ETA is honest arithmetic: straight-line distance, inflated by the
// road factor, at a conservative urban speed. The caller sees the
// assumptions, not a fake-precise countdown.
func ETA(distanceKm float64) (minutes int, assumptions string) {
	roadKm := distanceKm * RoadFactor
	minutes = int(math.Ceil(roadKm / UrbanSpeedKmh * 60))
	return minutes, fmt.Sprintf("%.1fkm straight-line x%.1f road factor at %.0fkm/h",
		distanceKm, RoadFactor, UrbanSpeedKmh)
}
