// facility-import (ADR-0015) seeds the facility registry from the source
// formats states actually have: CSV exports from DHIS2 / HFR (Health Facility
// Registry) or GeoJSON from GIS surveys. This is the answer to "capacity
// data population" — beds, doctors, nurses, amenities only render on the
// map if there is a governed path to get them in.
//
// The tool validates hard (a bad coordinate renders a facility in the
// Atlantic at 0,0 and poisons every /facilities/near answer), then upserts
// through the registry service API — never direct SQL — so the same code
// path validates demo seeds and production HFR imports.
//
//	facility-import -input data/facilities/ng-phc-sample.csv \
//	    -registry http://localhost:8081 [-dry-run]
//
// Input formats:
//   - CSV with header row (see data/facilities/ng-phc-sample.csv)
//   - GeoJSON FeatureCollection; facility fields in properties
package main

import (
	"bytes"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
)

// Facility mirrors services/registry-service store.Facility. Kept as a local
// struct (tools don't import services) with the same JSON contract.
type Facility struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	State     string   `json:"state"`
	LGA       string   `json:"lga"`
	Ownership string   `json:"ownership"`
	Level     string   `json:"level"`
	Services  []string `json:"services"`
	BHCPF     bool     `json:"bhcpf"`
	Power     string   `json:"power"`
	Water     string   `json:"water"`
	Latitude  *float64 `json:"latitude,omitempty"`
	Longitude *float64 `json:"longitude,omitempty"`
	Beds      int      `json:"beds"`
	Doctors   int      `json:"doctors"`
	Nurses    int      `json:"nurses"`
	Amenities []string `json:"amenities"`
	Phone     string   `json:"phone"`
	Hours     string   `json:"hours"`
}

// Nigeria bounding box (mainland). Anything outside is a data error, not a
// facility — the registry's 0,0 rule exists because of exactly this class
// of bad import.
const (
	ngLatMin, ngLatMax = 4.0, 14.2
	ngLngMin, ngLngMax = 2.6, 15.0
)

var knownAmenities = map[string]bool{
	"pharmacy": true, "lab": true, "maternity": true, "xray": true,
	"ambulance": true, "oxygen": true, "cold-chain": true,
}

type rowError struct {
	Row int    `json:"row"`
	ID  string `json:"id"`
	Err string `json:"error"`
}

func validate(f Facility, row int) *rowError {
	fail := func(format string, a ...any) *rowError {
		return &rowError{Row: row, ID: f.ID, Err: fmt.Sprintf(format, a...)}
	}
	if strings.TrimSpace(f.ID) == "" {
		return fail("id is required")
	}
	if strings.TrimSpace(f.Name) == "" {
		return fail("name is required")
	}
	if (f.Latitude == nil) != (f.Longitude == nil) {
		return fail("latitude and longitude must both be present or both absent")
	}
	if f.Latitude != nil {
		lat, lng := *f.Latitude, *f.Longitude
		if lat < ngLatMin || lat > ngLatMax || lng < ngLngMin || lng > ngLngMax {
			return fail("coordinate %.5f,%.5f outside Nigeria bounding box (null-island or CRS mixup?)", lat, lng)
		}
	}
	if f.Beds < 0 || f.Doctors < 0 || f.Nurses < 0 {
		return fail("beds/doctors/nurses must be >= 0")
	}
	return nil
}

// warn returns non-fatal data-quality notes (unknown amenity spellings etc).
func warn(f Facility, row int) []rowError {
	var out []rowError
	for _, a := range f.Amenities {
		if !knownAmenities[a] {
			out = append(out, rowError{Row: row, ID: f.ID,
				Err: "unknown amenity " + strconv.Quote(a) + " (known: pharmacy lab maternity xray ambulance oxygen cold-chain)"})
		}
	}
	return out
}

func parseFloat(s string) *float64 {
	s = strings.TrimSpace(s)
	if s == "" {
		return nil
	}
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return nil
	}
	return &v
}

func parseInt(s string) int {
	v, _ := strconv.Atoi(strings.TrimSpace(s))
	return v
}

func splitList(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ";") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func parseCSV(r io.Reader) ([]Facility, []rowError, error) {
	rd := csv.NewReader(r)
	rd.TrimLeadingSpace = true
	head, err := rd.Read()
	if err != nil {
		return nil, nil, fmt.Errorf("read header: %w", err)
	}
	col := map[string]int{}
	for i, h := range head {
		col[strings.ToLower(strings.TrimSpace(h))] = i
	}
	get := func(rec []string, name string) string {
		if i, ok := col[name]; ok && i < len(rec) {
			return rec[i]
		}
		return ""
	}
	var facs []Facility
	var errs []rowError
	row := 1
	for {
		rec, err := rd.Read()
		if err == io.EOF {
			break
		}
		row++
		if err != nil {
			errs = append(errs, rowError{Row: row, Err: "csv: " + err.Error()})
			continue
		}
		facs = append(facs, Facility{
			ID:        get(rec, "id"),
			Name:      get(rec, "name"),
			State:     get(rec, "state"),
			LGA:       get(rec, "lga"),
			Ownership: get(rec, "ownership"),
			Level:     get(rec, "level"),
			Services:  splitList(get(rec, "services")),
			BHCPF:     strings.EqualFold(strings.TrimSpace(get(rec, "bhcpf")), "true"),
			Power:     get(rec, "power"),
			Water:     get(rec, "water"),
			Latitude:  parseFloat(get(rec, "latitude")),
			Longitude: parseFloat(get(rec, "longitude")),
			Beds:      parseInt(get(rec, "beds")),
			Doctors:   parseInt(get(rec, "doctors")),
			Nurses:    parseInt(get(rec, "nurses")),
			Amenities: splitList(get(rec, "amenities")),
			Phone:     get(rec, "phone"),
			Hours:     get(rec, "hours"),
		})
	}
	return facs, errs, nil
}

type geoJSON struct {
	Features []struct {
		Geometry struct {
			Type        string    `json:"type"`
			Coordinates []float64 `json:"coordinates"`
		} `json:"geometry"`
		Properties map[string]any `json:"properties"`
	} `json:"features"`
}

func prop(props map[string]any, key string) string {
	if v, ok := props[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

func parseGeoJSON(r io.Reader) ([]Facility, []rowError, error) {
	var g geoJSON
	if err := json.NewDecoder(r).Decode(&g); err != nil {
		return nil, nil, fmt.Errorf("geojson: %w", err)
	}
	var facs []Facility
	for _, f := range g.Features {
		fac := Facility{
			ID:        prop(f.Properties, "id"),
			Name:      prop(f.Properties, "name"),
			State:     prop(f.Properties, "state"),
			LGA:       prop(f.Properties, "lga"),
			Ownership: prop(f.Properties, "ownership"),
			Level:     prop(f.Properties, "level"),
			Services:  splitList(prop(f.Properties, "services")),
			BHCPF:     strings.EqualFold(prop(f.Properties, "bhcpf"), "true"),
			Power:     prop(f.Properties, "power"),
			Water:     prop(f.Properties, "water"),
			Beds:      parseInt(prop(f.Properties, "beds")),
			Doctors:   parseInt(prop(f.Properties, "doctors")),
			Nurses:    parseInt(prop(f.Properties, "nurses")),
			Amenities: splitList(prop(f.Properties, "amenities")),
			Phone:     prop(f.Properties, "phone"),
			Hours:     prop(f.Properties, "hours"),
		}
		if f.Geometry.Type == "Point" && len(f.Geometry.Coordinates) >= 2 {
			lng, lat := f.Geometry.Coordinates[0], f.Geometry.Coordinates[1] // GeoJSON is lng,lat
			fac.Longitude, fac.Latitude = &lng, &lat
		}
		facs = append(facs, fac)
	}
	return facs, nil, nil
}

func main() {
	input := flag.String("input", "", "CSV or GeoJSON facility file (required)")
	registry := flag.String("registry", "http://localhost:8081", "registry service base URL")
	dryRun := flag.Bool("dry-run", false, "validate and print, do not POST")
	timeout := flag.Duration("timeout", 10*time.Second, "per-request timeout")
	flag.Parse()
	if *input == "" {
		fmt.Fprintln(os.Stderr, "facility-import: -input is required")
		os.Exit(2)
	}

	f, err := os.Open(*input)
	if err != nil {
		fmt.Fprintln(os.Stderr, "facility-import:", err)
		os.Exit(1)
	}
	defer f.Close()

	var facs []Facility
	var errs []rowError
	if strings.HasSuffix(strings.ToLower(*input), ".geojson") || strings.HasSuffix(strings.ToLower(*input), ".json") {
		facs, errs, err = parseGeoJSON(f)
	} else {
		facs, errs, err = parseCSV(f)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "facility-import:", err)
		os.Exit(1)
	}

	var valid []Facility
	var warnings []rowError
	for i, fac := range facs {
		if e := validate(fac, i+2); e != nil { // +2: header + 1-based
			errs = append(errs, *e)
			continue
		}
		warnings = append(warnings, warn(fac, i+2)...)
		valid = append(valid, fac)
	}

	imported := 0
	if !*dryRun {
		client := &http.Client{Timeout: *timeout}
		for _, fac := range valid {
			body, _ := json.Marshal(fac)
			resp, err := client.Post(*registry+"/facilities", "application/json", bytes.NewReader(body))
			if err != nil {
				errs = append(errs, rowError{ID: fac.ID, Err: "post: " + err.Error()})
				continue
			}
			if resp.StatusCode >= 300 {
				b, _ := io.ReadAll(io.LimitReader(resp.Body, 200))
				resp.Body.Close()
				errs = append(errs, rowError{ID: fac.ID, Err: fmt.Sprintf("post: HTTP %d %s", resp.StatusCode, b)})
				continue
			}
			resp.Body.Close()
			imported++
		}
	}

	report := map[string]any{
		"input":      *input,
		"dry_run":    *dryRun,
		"rows":       len(facs),
		"valid":      len(valid),
		"imported":   imported,
		"with_geo":   countGeo(valid),
		"total_beds": sumBeds(valid),
		"errors":     errs,
		"warnings":   warnings,
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	enc.Encode(report)
	if len(errs) > 0 {
		os.Exit(1) // a partially-failed import must fail the pipeline loudly
	}
}

func countGeo(facs []Facility) int {
	n := 0
	for _, f := range facs {
		if f.Latitude != nil {
			n++
		}
	}
	return n
}

func sumBeds(facs []Facility) int {
	n := 0
	for _, f := range facs {
		n += f.Beds
	}
	return n
}
