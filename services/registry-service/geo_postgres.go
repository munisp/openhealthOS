package main

// geo_postgres.go — PostGIS-backed spatial queries (ADR-0051).
//
// The Go-haversine path (geo.go) stays for the memory store, edge nodes
// and as an automatic fallback: if the geom column or the extension is
// missing (pre-0016 database), NearbyPostGIS returns ok=false and the
// caller degrades to the in-memory path — the API contract never
// changes, the database quietly gets faster when migrated.
//
// PostGIS does here what Go cannot at registry scale: GiST-indexed
// ST_DWithin (radius filter) and the KNN operator <-> (true nearest-
// neighbour ordering off the index, no full scan).

import (
	"context"
	"encoding/json"

	"github.com/jackc/pgx/v5"
)

// NearbyPostGIS answers "facilities within radiusKm of (lat,lng)",
// KNN-ordered, limit-capped, level/amenity filtered — index-assisted.
// ok=false = caller must use the haversine fallback (feature-detected,
// never assumed).
func (p *PostgresStore) NearbyPostGIS(ctx context.Context, lat, lng, radiusKm float64, level, amenity string, limit int) ([]FacilityDistance, bool) {
	if limit <= 0 {
		limit = 20
	}
	q := `
		SELECT ` + facilityCols + `,
		       ST_Distance(geom, ST_SetSRID(ST_MakePoint($1,$2),4326)::geography)/1000.0 AS dist_km
		FROM facilities
		WHERE geom IS NOT NULL
		  AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint($1,$2),4326)::geography, $3)`
	args := []any{lng, lat, radiusKm * 1000} // ST_DWithin takes metres
	if level != "" {
		q += ` AND level = $` + itoa(len(args)+1)
		args = append(args, level)
	}
	if amenity != "" {
		q += ` AND amenities @> $` + itoa(len(args)+1) + `::jsonb`
		args = append(args, `["`+amenity+`"]`)
	}
	q += ` ORDER BY geom <-> ST_SetSRID(ST_MakePoint($1,$2),4326)::geography LIMIT $` + itoa(len(args)+1)
	args = append(args, limit)

	rows, err := p.pool.Query(ctx, q, args...)
	if err != nil {
		// undefined_column / undefined_function (no PostGIS yet) → fallback.
		return nil, false
	}
	defer rows.Close()
	var out []FacilityDistance
	for rows.Next() {
		f, dist, err := scanFacilityDist(rows)
		if err != nil {
			return nil, false
		}
		out = append(out, FacilityDistance{Facility: f, DistanceKm: dist})
	}
	return out, true
}

func itoa(i int) string {
	const digits = "0123456789"
	if i == 0 {
		return "0"
	}
	var b []byte
	for i > 0 {
		b = append([]byte{digits[i%10]}, b...)
		i /= 10
	}
	return string(b)
}

// scanFacilityDist scans facilityCols + a trailing float distance.
func scanFacilityDist(row pgx.Row) (Facility, float64, error) {
	var f Facility
	var services, amenities []byte
	var lat, lon *float64
	var dist float64
	err := row.Scan(&f.ID, &f.Name, &f.State, &f.LGA, &f.Ownership, &f.Level,
		&services, &f.BHCPF, &f.Power, &f.Water, &lat, &lon,
		&f.Beds, &f.Doctors, &f.Nurses, &amenities, &f.Phone, &f.Hours, &dist)
	if err != nil {
		return f, 0, err
	}
	json.Unmarshal(services, &f.Services)
	json.Unmarshal(amenities, &f.Amenities)
	if lat != nil {
		f.Latitude = *lat
	}
	if lon != nil {
		f.Longitude = *lon
	}
	return f, dist, nil
}
