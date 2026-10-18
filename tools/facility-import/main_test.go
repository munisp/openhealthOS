package main

import (
	"os"
	"strings"
	"testing"
)

func f64(v float64) *float64 { return &v }

func openTestFile(t *testing.T, path string) *os.File {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatalf("open %s: %v", path, err)
	}
	t.Cleanup(func() { f.Close() })
	return f
}

func TestValidateRejectsNullIsland(t *testing.T) {
	f := Facility{ID: "F1", Name: "Clinic", Latitude: f64(0), Longitude: f64(0)}
	if e := validate(f, 2); e == nil {
		t.Fatal("0,0 must be rejected — that is the null-island bug class")
	}
}

func TestValidateRejectsHalfCoordinate(t *testing.T) {
	f := Facility{ID: "F1", Name: "Clinic", Latitude: f64(9.08)} // no longitude
	if e := validate(f, 2); e == nil {
		t.Fatal("latitude without longitude must be rejected")
	}
}

func TestValidateAcceptsUngelocated(t *testing.T) {
	// Facilities without coordinates are legal — the map and /facilities/near
	// simply never route to them (registry's unplaced rule).
	f := Facility{ID: "F1", Name: "Clinic"}
	if e := validate(f, 2); e != nil {
		t.Fatalf("unplaced facility should validate, got %+v", e)
	}
}

func TestValidateRejectsOutsideNigeria(t *testing.T) {
	f := Facility{ID: "F1", Name: "Clinic", Latitude: f64(51.5), Longitude: f64(-0.12)} // London
	if e := validate(f, 2); e == nil {
		t.Fatal("London coordinates must be rejected for a Nigeria registry")
	}
}

func TestValidateRejectsNegativeCapacity(t *testing.T) {
	f := Facility{ID: "F1", Name: "Clinic", Beds: -3}
	if e := validate(f, 2); e == nil {
		t.Fatal("negative beds must be rejected")
	}
}

func TestParseCSV(t *testing.T) {
	csvText := `id,name,state,lga,ownership,level,services,bhcpf,power,water,latitude,longitude,beds,doctors,nurses,amenities,phone,hours
F1,Test PHC,Kano,Nassarawa,public,primary,opd;maternity,true,solar,borehole,12.0022,8.5920,12,1,4,pharmacy;maternity,+2348000000000,24/7
F2,No Geo PHC,Lagos,Ikeja,public,primary,opd,false,grid,none,,,,,,,,
`
	facs, errs, err := parseCSV(strings.NewReader(csvText))
	if err != nil || len(errs) != 0 {
		t.Fatalf("parse: %v %v", err, errs)
	}
	if len(facs) != 2 {
		t.Fatalf("want 2 facilities, got %d", len(facs))
	}
	if facs[0].Latitude == nil || *facs[0].Latitude != 12.0022 {
		t.Fatalf("lat parse: %+v", facs[0].Latitude)
	}
	if facs[0].Beds != 12 || facs[0].Doctors != 1 || facs[0].Nurses != 4 {
		t.Fatalf("capacity parse: %+v", facs[0])
	}
	if len(facs[0].Amenities) != 2 || facs[0].Amenities[1] != "maternity" {
		t.Fatalf("amenities parse: %v", facs[0].Amenities)
	}
	if !facs[0].BHCPF {
		t.Fatal("bhcpf bool parse")
	}
	if facs[1].Latitude != nil || facs[1].Longitude != nil {
		t.Fatal("blank coords must stay nil (unplaced), not 0")
	}
}

func TestParseGeoJSON(t *testing.T) {
	geo := `{"type":"FeatureCollection","features":[
	  {"type":"Feature","geometry":{"type":"Point","coordinates":[8.5920,12.0022]},
	   "properties":{"id":"F9","name":"Geo PHC","state":"Kano","beds":"8","amenities":"lab;oxygen"}}
	]}`
	facs, _, err := parseGeoJSON(strings.NewReader(geo))
	if err != nil {
		t.Fatalf("parse: %v", err)
	}
	if len(facs) != 1 {
		t.Fatalf("want 1, got %d", len(facs))
	}
	// GeoJSON order is lng,lat — the import must not swap them.
	if *facs[0].Latitude != 12.0022 || *facs[0].Longitude != 8.5920 {
		t.Fatalf("lng,lat order: lat=%v lng=%v", *facs[0].Latitude, *facs[0].Longitude)
	}
	if facs[0].Beds != 8 {
		t.Fatalf("beds: %d", facs[0].Beds)
	}
}

func TestWarnUnknownAmenity(t *testing.T) {
	f := Facility{ID: "F1", Name: "C", Amenities: []string{"pharmcy"}} // typo
	w := warn(f, 2)
	if len(w) != 1 {
		t.Fatalf("typo amenity should warn, got %v", w)
	}
}

func TestSampleDatasetIsValid(t *testing.T) {
	// The checked-in seed must always pass its own importer — CI for data.
	facs, errs, err := parseCSV(openTestFile(t, "../../data/facilities/ng-phc-sample.csv"))
	if err != nil {
		t.Fatalf("sample parse: %v", err)
	}
	if len(errs) != 0 {
		t.Fatalf("sample csv errors: %v", errs)
	}
	if len(facs) < 30 {
		t.Fatalf("sample should carry >=30 facilities, got %d", len(facs))
	}
	for i, fac := range facs {
		if e := validate(fac, i+2); e != nil {
			t.Fatalf("sample row %d (%s): %s", i+2, fac.ID, e.Err)
		}
	}
	geo := countGeo(facs)
	if geo < len(facs)*9/10 {
		t.Fatalf("sample should be >=90%% geolocated, got %d/%d", geo, len(facs))
	}
	if sumBeds(facs) == 0 {
		t.Fatal("sample must carry real capacity numbers")
	}
}
