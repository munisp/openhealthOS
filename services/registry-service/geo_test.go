package main

// Geospatial query tests (ADR-0014): haversine correctness, radius/limit/
// filter behaviour, and the HTTP surface.

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHaversineKnownDistances(t *testing.T) {
	// Lagos (6.5244, 3.3792) → Abuja (9.0765, 7.3986) ≈ 540 km.
	d := HaversineKm(6.5244, 3.3792, 9.0765, 7.3986)
	if d < 500 || d > 580 {
		t.Fatalf("lagos→abuja: got %.1f km, want ~540", d)
	}
	// Same point is zero.
	if d := HaversineKm(10.5, 7.4, 10.5, 7.4); d != 0 {
		t.Fatalf("same point: got %f", d)
	}
	// ~1 degree of latitude ≈ 111 km anywhere.
	d = HaversineKm(10.0, 7.0, 11.0, 7.0)
	if d < 109 || d > 113 {
		t.Fatalf("1deg latitude: got %.1f km", d)
	}
}

func geoFixture() []Facility {
	return []Facility{
		{ID: "PHC-NEAR", Name: "Kawo PHC", Level: "PHC", Latitude: 10.545, Longitude: 7.435,
			Beds: 12, Nurses: 6, Amenities: []string{"pharmacy", "maternity"}},
		{ID: "HOSP-MID", Name: "General Hospital", Level: "hospital", Latitude: 10.600, Longitude: 7.440,
			Beds: 250, Doctors: 18, Nurses: 60, Amenities: []string{"pharmacy", "lab", "xray", "ambulance"}},
		{ID: "PHC-FAR", Name: "Distant PHC", Level: "PHC", Latitude: 12.0, Longitude: 8.9,
			Amenities: []string{"pharmacy"}},
		{ID: "UNPLACED", Name: "No Coords Clinic", Level: "clinic"}, // excluded: no coordinates
	}
}

func TestNearbyFacilities(t *testing.T) {
	all := geoFixture()
	// Query from Kaduna city centre (10.52, 7.43): NEAR ~3 km, MID ~9 km,
	// FAR >150 km.
	near := NearbyFacilities(all, 10.52, 7.43, 50, "", "", 0)
	if len(near) != 2 {
		t.Fatalf("want 2 within 50km, got %d: %+v", len(near), near)
	}
	if near[0].ID != "PHC-NEAR" || near[1].ID != "HOSP-MID" {
		t.Fatalf("sorted by distance: %+v", []string{near[0].ID, near[1].ID})
	}
	if near[0].DistanceKm <= 0 || near[0].DistanceKm >= near[1].DistanceKm {
		t.Fatalf("distances wrong: %+v", near)
	}

	// Radius cuts the far one; the unplaced one never appears at any radius.
	far := NearbyFacilities(all, 10.52, 7.43, 500, "", "", 0)
	for _, f := range far {
		if f.ID == "UNPLACED" {
			t.Fatal("unplaced facility must never be routable")
		}
	}
	if len(far) != 3 {
		t.Fatalf("500km should catch 3 placed facilities, got %d", len(far))
	}

	// Filters: level and amenity.
	hospitals := NearbyFacilities(all, 10.52, 7.43, 50, "hospital", "", 0)
	if len(hospitals) != 1 || hospitals[0].ID != "HOSP-MID" {
		t.Fatalf("level filter: %+v", hospitals)
	}
	xray := NearbyFacilities(all, 10.52, 7.43, 50, "", "xray", 0)
	if len(xray) != 1 || xray[0].ID != "HOSP-MID" {
		t.Fatalf("amenity filter: %+v", xray)
	}

	// Limit caps results.
	capped := NearbyFacilities(all, 10.52, 7.43, 50, "", "", 1)
	if len(capped) != 1 || capped[0].ID != "PHC-NEAR" {
		t.Fatalf("limit: %+v", capped)
	}
}

func TestFacilitiesNearHTTP(t *testing.T) {
	store := NewMemoryStore()
	for _, f := range geoFixture() {
		_ = store.PutFacility(f)
	}
	mux := server{store: store}.routes()

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet,
		"/facilities/near?lat=10.52&lng=7.43&radius_km=50", nil))
	if rec.Code != 200 {
		t.Fatalf("status %d", rec.Code)
	}
	var out struct {
		Results []FacilityDistance `json:"results"`
	}
	json.NewDecoder(rec.Body).Decode(&out)
	if len(out.Results) != 2 || out.Results[0].ID != "PHC-NEAR" {
		t.Fatalf("near results: %+v", out)
	}
	if out.Results[0].Beds != 12 || out.Results[1].Doctors != 18 {
		t.Fatalf("capacity profile rides along: %+v", out.Results)
	}

	// Malformed/absent coordinates → 400, never a guess.
	for _, bad := range []string{
		"/facilities/near", "/facilities/near?lat=abc&lng=7",
		"/facilities/near?lat=10.5&lng=7.4&radius_km=-3",
		"/facilities/near?lat=95&lng=7.4",
	} {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, bad, nil))
		if rec.Code != 400 {
			t.Fatalf("%s: want 400, got %d", bad, rec.Code)
		}
	}
}
