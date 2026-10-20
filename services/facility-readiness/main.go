// facility-readiness (innovation 8): a live "can this facility actually
// deliver care today?" index. Equipment/telemetry readings stream in
// (cold-chain temperature, power uptime, water availability); the service
// keeps a rolling window per facility and computes a 0–100 readiness
// score with named components, so dashboards, the referral router and
// LGA hubs can steer patients away from facilities whose fridge is warm
// or whose generator is dead — the #1 silent cause of failed referrals.
//
// Scoring (each component 0–100, weighted):
//
//	coldchain 45% — % of temperature readings inside 2–8 °C (WHO EPI range)
//	power     35% — % uptime readings
//	water     20% — % availability readings
//
// Stdlib-only (ADR-0005); in-memory window with disk snapshot optional
// later. Readings older than the window fall off; a facility with no
// readings scores -1 (unknown) rather than a falsely reassuring 0.
package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// windowSize bounds memory per facility per kind: at one reading per
// minute this is ~3 days of history.
const windowSize = 4320

// Reading kinds.
const (
	KindColdchain = "coldchain_temp" // value: celsius
	KindPower     = "power"          // value: 1 = grid/genny up, 0 = down
	KindWater     = "water"          // value: 1 = available, 0 = none
)

// Weights.
const (
	wCold = 45
	wPow  = 35
	wWat  = 20
)

type Reading struct {
	FacilityID string  `json:"facility_id"`
	Kind       string  `json:"kind"`
	Value      float64 `json:"value"`
	TS         string  `json:"ts,omitempty"`
}

type Component struct {
	Score    float64 `json:"score"`     // 0–100, -1 when no data
	Readings int     `json:"readings"`
	Good     int     `json:"good"`
}

type Readiness struct {
	FacilityID string              `json:"facility_id"`
	Score      float64             `json:"score"` // -1 = unknown
	Grade      string              `json:"grade"`
	Components map[string]Component `json:"components"`
	ComputedAt string              `json:"computed_at"`
}

type Store struct {
	mu   sync.Mutex
	data map[string]map[string][]float64 // facility → kind → values
}

func NewStore() *Store { return &Store{data: map[string]map[string][]float64{}} }

func (s *Store) Add(r Reading) {
	s.mu.Lock()
	defer s.mu.Unlock()
	fk, ok := s.data[r.FacilityID]
	if !ok {
		fk = map[string][]float64{}
		s.data[r.FacilityID] = fk
	}
	vals := append(fk[r.Kind], r.Value)
	if len(vals) > windowSize {
		vals = vals[len(vals)-windowSize:]
	}
	fk[r.Kind] = vals
}

func (s *Store) Facilities() []string {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]string, 0, len(s.data))
	for f := range s.data {
		out = append(out, f)
	}
	sort.Strings(out)
	return out
}

// scoreWindow returns (score, good) — percentage of values satisfying ok.
// No data → (-1, 0) so callers can report "unknown" honestly.
func scoreWindow(vals []float64, ok func(float64) bool) (float64, int) {
	if len(vals) == 0 {
		return -1, 0
	}
	good := 0
	for _, v := range vals {
		if ok(v) {
			good++
		}
	}
	return float64(good) * 100 / float64(len(vals)), good
}

func coldOK(t float64) bool { return t >= 2 && t <= 8 }
func upOK(v float64) bool   { return v > 0 }

// Readiness computes the weighted index. A missing component is re-
// distributed across the components that do have data, so a facility
// with no water sensor isn't penalised — but all-missing is unknown.
func (s *Store) Readiness(facilityID string) Readiness {
	s.mu.Lock()
	fk := s.data[facilityID]
	vals := map[string][]float64{}
	for k, v := range fk {
		vals[k] = append([]float64(nil), v...)
	}
	s.mu.Unlock()

	comps := map[string]Component{}
	weights := map[string]int{KindColdchain: wCold, KindPower: wPow, KindWater: wWat}
	okFns := map[string]func(float64) bool{KindColdchain: coldOK, KindPower: upOK, KindWater: upOK}

	totalW, acc := 0, 0.0
	for _, kind := range []string{KindColdchain, KindPower, KindWater} {
		sc, good := scoreWindow(vals[kind], okFns[kind])
		n := len(vals[kind])
		comps[kind] = Component{Score: sc, Readings: n, Good: good}
		if sc >= 0 {
			totalW += weights[kind]
			acc += sc * float64(weights[kind])
		}
	}
	score := -1.0
	if totalW > 0 {
		score = acc / float64(totalW)
	}
	return Readiness{
		FacilityID: facilityID, Score: score, Grade: grade(score),
		Components: comps,
		ComputedAt: time.Now().UTC().Format(time.RFC3339),
	}
}

// grade: referral routers and dashboards need a word, not a float.
func grade(score float64) string {
	switch {
	case score < 0:
		return "UNKNOWN"
	case score >= 80:
		return "READY"
	case score >= 50:
		return "DEGRADED"
	default:
		return "NOT_READY"
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func main() {
	addr := ":8093"
	if v := os.Getenv("ADDR"); v != "" {
		addr = v
	}
	store := NewStore()
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, map[string]string{"status": "ok"}) })
	mux.HandleFunc("/readings", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSON(w, 405, map[string]string{"error": "method not allowed"})
			return
		}
		var rd Reading
		if err := json.NewDecoder(r.Body).Decode(&rd); err != nil {
			writeJSON(w, 400, map[string]string{"error": "bad json"})
			return
		}
		if rd.FacilityID == "" || rd.Kind == "" {
			writeJSON(w, 400, map[string]string{"error": "facility_id and kind required"})
			return
		}
		if rd.Kind != KindColdchain && rd.Kind != KindPower && rd.Kind != KindWater {
			writeJSON(w, 400, map[string]string{"error": "kind must be coldchain_temp|power|water"})
			return
		}
		store.Add(rd)
		writeJSON(w, 202, map[string]string{"status": "accepted"})
	})
	mux.HandleFunc("/readiness/", func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimPrefix(r.URL.Path, "/readiness/")
		if id == "" {
			writeJSON(w, 400, map[string]string{"error": "facility id required"})
			return
		}
		writeJSON(w, 200, store.Readiness(id))
	})
	mux.HandleFunc("/readiness", func(w http.ResponseWriter, r *http.Request) {
		out := []Readiness{}
		for _, f := range store.Facilities() {
			out = append(out, store.Readiness(f))
		}
		writeJSON(w, 200, map[string]any{"facilities": out})
	})
	log.Printf("facility-readiness listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}
