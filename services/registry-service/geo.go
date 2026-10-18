package main

// Geospatial queries (ADR-0014): "facilities near me" answered by the
// registry itself — haversine over indexed coordinates, no GIS extension
// required at this scale (tens of thousands of facilities scan in
// microseconds; the bounding-box index covers the Postgres plane).
//
// Why not CesiumJS / a tile server: the clients are $100 Android phones on
// 2G. The map is a self-contained canvas render over a bundled Nigeria
// boundary — zero network, zero WebGL. Apache Sedona/Spark belongs to
// national-scale batch analytics on the state plane, not the read path.

import (
	"math"
	"sort"
	"strconv"
)

// earthRadiusKm — mean radius, haversine.
const earthRadiusKm = 6371.0088

// HaversineKm is the great-circle distance between two coordinates.
func HaversineKm(lat1, lng1, lat2, lng2 float64) float64 {
	rad := math.Pi / 180
	dLat := (lat2 - lat1) * rad
	dLng := (lng2 - lng1) * rad
	a := math.Sin(dLat/2)*math.Sin(dLat/2) +
		math.Cos(lat1*rad)*math.Cos(lat2*rad)*math.Sin(dLng/2)*math.Sin(dLng/2)
	return 2 * earthRadiusKm * math.Asin(math.Sqrt(a))
}

// FacilityDistance pairs a facility with its distance from a query point.
type FacilityDistance struct {
	Facility
	DistanceKm float64 `json:"distance_km"`
}

// NearbyFacilities returns facilities within radiusKm of (lat, lng),
// sorted by distance, capped at limit. Filters: level and required
// amenity are optional ("" = any). Facilities without coordinates are
// excluded — you cannot route to a facility you cannot place.
func NearbyFacilities(all []Facility, lat, lng, radiusKm float64, level, amenity string, limit int) []FacilityDistance {
	out := []FacilityDistance{}
	for _, f := range all {
		if f.Latitude == 0 && f.Longitude == 0 {
			continue // unplaced
		}
		if level != "" && f.Level != level {
			continue
		}
		if amenity != "" && !hasAmenity(f, amenity) {
			continue
		}
		d := HaversineKm(lat, lng, f.Latitude, f.Longitude)
		if d <= radiusKm {
			out = append(out, FacilityDistance{f, math.Round(d*100) / 100})
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].DistanceKm < out[j].DistanceKm })
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

func hasAmenity(f Facility, a string) bool {
	for _, x := range f.Amenities {
		if x == a {
			return true
		}
	}
	return false
}

// parseGeoParams extracts and validates lat/lng/radius/limit from query
// values. ok=false means the coordinates were absent or malformed — the
// handler answers 400 rather than guessing.
func parseGeoParams(q map[string][]string) (lat, lng, radius float64, limit int, ok bool) {
	get := func(k string) string {
		if v := q[k]; len(v) > 0 {
			return v[0]
		}
		return ""
	}
	var err error
	if lat, err = strconv.ParseFloat(get("lat"), 64); err != nil {
		return 0, 0, 0, 0, false
	}
	if lng, err = strconv.ParseFloat(get("lng"), 64); err != nil {
		return 0, 0, 0, 0, false
	}
	if lat < -90 || lat > 90 || lng < -180 || lng > 180 {
		return 0, 0, 0, 0, false
	}
	radius = 25 // km, sensible default for "near me" in a Nigerian LGA
	radiusRaw := get("radius_km")
	if radiusRaw == "" {
		radiusRaw = get("radius") // alias — the patient app sends this
	}
	if radiusRaw != "" {
		if radius, err = strconv.ParseFloat(radiusRaw, 64); err != nil || radius <= 0 || radius > 500 {
			return 0, 0, 0, 0, false
		}
	}
	limit = 50
	if v := get("limit"); v != "" {
		if limit, err = strconv.Atoi(v); err != nil || limit <= 0 || limit > 500 {
			return 0, 0, 0, 0, false
		}
	}
	return lat, lng, radius, limit, true
}
