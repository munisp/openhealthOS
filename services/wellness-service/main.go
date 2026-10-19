// Wellness service (ADR-0035) — the patient's phone as a preventive-health
// sensor. Normalized device and self-logged observations (steps, heart
// rate, workouts, sleep, nutrition, weight) are ingested from companion
// syncs (Health Connect on Android, Apple Health on iOS, Fitbit cloud
// webhooks, generic IoT bridges) or entered by hand, then become FHIR
// Observations in the shared health record — device data is part of the
// medical record, with provenance.
//
// Doctrine:
//   - allow-list of sources, never a deny-list (same as wallets):
//     an unregistered source is refused before anything is stored.
//   - exactly-once: client_ref dedupes per patient+source; deterministic
//     ids make replays idempotent.
//   - offline-first: the local JSONL journal is the service's source of
//     truth; mirroring into the SHR is best-effort and retried by replay.
//   - prevention plan comes from packages/pophealth (subprocess seam,
//     same pattern as benefits/economics): wellness observations are
//     merged into the patient's SHR bundle so the plan sees BOTH the
//     clinical record and the lifestyle data.
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// ---------------------------------------------------------------- model

// Source allow-list: each registered source declares how data arrives.
var SOURCES = map[string]map[string]string{
	"manual":         {"label": "Manual entry", "path": "Entered in the patient app"},
	"health-connect": {"label": "Android Health Connect", "path": "Companion sync reads Health Connect on-device, posts normalized batches"},
	"apple-health":   {"label": "Apple Health", "path": "Companion sync reads HealthKit on-device, posts normalized batches"},
	"fitbit":         {"label": "Fitbit", "path": "Fitbit Web API subscription webhook -> normalized ingest (OAuth held by the companion, never the platform)"},
	"generic-iot":    {"label": "Generic IoT bridge", "path": "Facility/community devices (BP cuffs, scales) bridged by a gateway app"},
}

// Observation type map: wellness type -> FHIR coding. LOINC where one
// exists, OH-* local concepts otherwise (exported in pophealth concepts).
var TYPE_MAP = map[string]struct {
	code, display string
	numeric       bool // measurement vs observation (string payload)
}{
	"steps":      {"55423-8", "Number of steps in 24 hr Measured", true},
	"heart-rate": {"8867-4", "Heart rate", true},
	"weight":     {"29463-7", "Body weight", true},
	"bmi":        {"39156-5", "Body mass index (BMI)", true},
	"sleep":      {"OH-SLEEP", "Sleep duration (hours)", true},
	"workout":    {"OH-WORKOUT", "Workout session", false},
	"nutrition":  {"OH-NUTRITION-LOG", "Nutrition log entry", false},
}

var (
	ErrUnknownSource = errors.New("unknown source — the allow-list is the policy")
	ErrUnknownType   = errors.New("unknown observation type")
	ErrBadRequest    = errors.New("patient_id, source and observations required")
)

type IngestObs struct {
	Type  string         `json:"type"`
	Value float64        `json:"value,omitempty"`
	Text  string         `json:"text,omitempty"`
	Unit  string         `json:"unit,omitempty"`
	Ts    string         `json:"ts"`
	Ref   string         `json:"ref,omitempty"` // exactly-once client ref
	Meta  map[string]any `json:"meta,omitempty"`
}

type IngestRequest struct {
	PatientID    string      `json:"patient_id"`
	Source       string      `json:"source"`
	Observations []IngestObs `json:"observations"`
}

type StoredObs struct {
	ID        string  `json:"id"`
	PatientID string  `json:"patient_id"`
	Source    string  `json:"source"`
	Type      string  `json:"type"`
	Value     float64 `json:"value,omitempty"`
	Text      string  `json:"text,omitempty"`
	Unit      string  `json:"unit,omitempty"`
	Ts        string  `json:"ts"`
	Ref       string  `json:"ref,omitempty"`
	FHIRID    string  `json:"fhir_id"`
	Mirrored  bool    `json:"mirrored"`
}

func obsID(patient, source, ref string, o IngestObs) string {
	key := ref
	if key == "" {
		key = fmt.Sprintf("%s|%s|%v|%s", o.Type, o.Ts, o.Value, o.Text)
	}
	sum := sha256.Sum256([]byte(patient + "|" + source + "|" + key))
	return "wo-" + hex.EncodeToString(sum[:8])
}

// toFHIR renders the FHIR Observation that lands in the medical record.
func (o StoredObs) toFHIR() map[string]any {
	t := TYPE_MAP[o.Type]
	body := map[string]any{
		"resourceType": "Observation",
		"id":           o.FHIRID,
		"status":       "final",
		"category": []map[string]any{{"coding": []map[string]string{
			{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
				"code": "activity"}}}},
		"code": map[string]any{"coding": []map[string]string{{
			"system":  "http://loinc.org",
			"code":    t.code,
			"display": t.display,
		}}, "text": t.display},
		"subject":           map[string]string{"reference": "Patient/" + o.PatientID},
		"effectiveDateTime": o.Ts,
		"performer":         []map[string]any{{"display": "wellness:" + o.Source}},
		"patient_id":        o.PatientID,     // SHR envelope fields
		"source_facility":   "wellness-rail", // provenance: arrived via the wellness rail
	}
	if t.numeric {
		body["valueQuantity"] = map[string]any{"value": o.Value, "unit": o.Unit}
	} else {
		body["valueString"] = o.Text
	}
	return body
}

// ---------------------------------------------------------------- store

type Store struct {
	mu   sync.Mutex
	obs  map[string]StoredObs // by id
	refs map[string]string    // patient|source|ref -> id
	path string               // JSONL journal; "" = in-memory only
}

func NewStore(dir string) (*Store, error) {
	s := &Store{obs: map[string]StoredObs{}, refs: map[string]string{}}
	if dir == "" {
		return s, nil
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	s.path = filepath.Join(dir, "wellness.jsonl")
	if f, err := os.Open(s.path); err == nil {
		defer f.Close()
		dec := json.NewDecoder(f)
		for {
			var o StoredObs
			if dec.Decode(&o) != nil {
				break
			}
			s.obs[o.ID] = o
			if o.Ref != "" {
				s.refs[o.PatientID+"|"+o.Source+"|"+o.Ref] = o.ID
			}
		}
	}
	return s, nil
}

// Add is idempotent: a known (patient, source, ref) returns the existing row.
func (s *Store) Add(o StoredObs) (StoredObs, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if o.Ref != "" {
		if id, ok := s.refs[o.PatientID+"|"+o.Source+"|"+o.Ref]; ok {
			return s.obs[id], false, nil
		}
	}
	s.obs[o.ID] = o
	if o.Ref != "" {
		s.refs[o.PatientID+"|"+o.Source+"|"+o.Ref] = o.ID
	}
	if s.path != "" {
		f, err := os.OpenFile(s.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
		if err != nil {
			return o, true, err
		}
		defer f.Close()
		json.NewEncoder(f).Encode(o)
	}
	return o, true, nil
}

func (s *Store) ForPatient(patient string) []StoredObs {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []StoredObs{}
	for _, o := range s.obs {
		if o.PatientID == patient {
			out = append(out, o)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Ts < out[j].Ts })
	return out
}

// ---------------------------------------------------------------- SHR mirror

type Mirror interface {
	Put(patient string, fhir map[string]any) error
}

type HTTPMirror struct {
	BaseURL string
	Client  *http.Client
}

func (m HTTPMirror) Put(patient string, fhir map[string]any) error {
	id, _ := fhir["id"].(string)
	rt, _ := fhir["resourceType"].(string)
	body, _ := json.Marshal(fhir)
	req, err := http.NewRequest(http.MethodPut,
		strings.TrimSuffix(m.BaseURL, "/")+"/fhir/"+rt+"/"+id, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := m.client().Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode >= 300 {
		return fmt.Errorf("shr status %d", resp.StatusCode)
	}
	return nil
}

func (m HTTPMirror) client() *http.Client {
	if m.Client != nil {
		return m.Client
	}
	return &http.Client{Timeout: 3 * time.Second}
}

// ---------------------------------------------------------------- planner

type Planner interface {
	Plan(patient string, bundle map[string]any) (map[string]any, error)
}

// SubprocessPlanner runs packages/pophealth — same seam as
// benefits/economics in claims-orchestrator.
type SubprocessPlanner struct {
	Python, Script, Dir string // Dir: scratch for the bundle file
}

func (p SubprocessPlanner) Plan(patient string, bundle map[string]any) (map[string]any, error) {
	data, _ := json.Marshal(bundle)
	f, err := os.CreateTemp(p.Dir, "wellness-bundle-*.json")
	if err != nil {
		return nil, err
	}
	defer os.Remove(f.Name())
	if _, err := f.Write(data); err != nil {
		f.Close()
		return nil, err
	}
	f.Close()
	cmd := exec.Command(p.Python, p.Script, "plan", "--bundle", f.Name(), "--patient", patient)
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("pophealth plan: %v", err)
	}
	var plan map[string]any
	if err := json.Unmarshal(out, &plan); err != nil {
		return nil, fmt.Errorf("pophealth plan json: %v", err)
	}
	return plan, nil
}

// ---------------------------------------------------------------- service

type Service struct {
	store   *Store
	mirror  Mirror
	planner Planner
}

func (s *Service) ingest(req IngestRequest) (map[string]any, error) {
	if req.PatientID == "" || req.Source == "" || len(req.Observations) == 0 {
		return nil, ErrBadRequest
	}
	if _, ok := SOURCES[req.Source]; !ok {
		return nil, ErrUnknownSource
	}
	accepted, deduped := 0, 0
	ids := []string{}
	for _, io_ := range req.Observations {
		if _, ok := TYPE_MAP[io_.Type]; !ok {
			return nil, ErrUnknownType
		}
		if io_.Ts == "" {
			io_.Ts = time.Now().UTC().Format(time.RFC3339)
		}
		o := StoredObs{
			ID:        obsID(req.PatientID, req.Source, io_.Ref, io_),
			PatientID: req.PatientID, Source: req.Source, Type: io_.Type,
			Value: io_.Value, Text: io_.Text, Unit: io_.Unit, Ts: io_.Ts, Ref: io_.Ref,
		}
		o.FHIRID = o.ID
		stored, fresh, err := s.store.Add(o)
		if err != nil {
			return nil, err
		}
		if !fresh {
			deduped++
			ids = append(ids, stored.ID)
			continue
		}
		if s.mirror != nil {
			if err := s.mirror.Put(req.PatientID, stored.toFHIR()); err == nil {
				stored.Mirrored = true
				s.store.Add(stored) // journal the mirror flag
			} else {
				log.Printf("shr mirror deferred for %s: %v", stored.ID, err)
			}
		}
		accepted++
		ids = append(ids, stored.ID)
	}
	return map[string]any{
		"accepted": accepted, "deduped": deduped, "ids": ids,
	}, nil
}

// summary aggregates the patient's wellness journal — the numbers the
// app's wellness tab renders.
func (s *Service) summary(patient string, now time.Time) map[string]any {
	obs := s.store.ForPatient(patient)
	day := 24 * time.Hour
	dayStart := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	stepsToday, steps14 := 0.0, 0.0
	stepsDays := map[string]bool{}
	workouts7, nutritionDays7 := 0, map[string]bool{}
	sleepSum, sleepN := 0.0, 0
	var lastWeight, lastHR *StoredObs
	for i := range obs {
		o := &obs[i]
		ts, err := time.Parse(time.RFC3339, o.Ts)
		if err != nil {
			if d, err2 := time.Parse("2006-01-02", o.Ts); err2 == nil {
				ts = d
			} else {
				continue
			}
		}
		age := now.Sub(ts)
		switch o.Type {
		case "steps":
			if !ts.Before(dayStart) {
				stepsToday += o.Value
			}
			if age <= 14*day {
				key := ts.Format("2006-01-02")
				if !stepsDays[key] {
					stepsDays[key] = true
				}
				steps14 += o.Value
			}
		case "workout":
			if age <= 7*day {
				workouts7++
			}
		case "nutrition":
			if age <= 7*day {
				nutritionDays7[ts.Format("2006-01-02")] = true
			}
		case "sleep":
			if age <= 7*day {
				sleepSum += o.Value
				sleepN++
			}
		case "weight":
			if lastWeight == nil || o.Ts > lastWeight.Ts {
				lastWeight = o
			}
		case "heart-rate":
			if lastHR == nil || o.Ts > lastHR.Ts {
				lastHR = o
			}
		}
	}
	avgSteps14 := 0.0
	if len(stepsDays) > 0 {
		avgSteps14 = steps14 / float64(len(stepsDays))
	}
	out := map[string]any{
		"patient":             patient,
		"steps_today":         stepsToday,
		"avg_daily_steps_14d": math_round(avgSteps14),
		"workouts_7d":         workouts7,
		"nutrition_days_7d":   len(nutritionDays7),
		"observations":        len(obs),
		"sources":             s.sourcesFor(patient),
	}
	if sleepN > 0 {
		out["avg_sleep_hours_7d"] = math_round(sleepSum / float64(sleepN))
	}
	if lastWeight != nil {
		out["latest_weight"] = map[string]any{"value": lastWeight.Value, "unit": lastWeight.Unit, "ts": lastWeight.Ts}
	}
	if lastHR != nil {
		out["latest_heart_rate"] = map[string]any{"value": lastHR.Value, "unit": lastHR.Unit, "ts": lastHR.Ts}
	}
	return out
}

func math_round(x float64) float64 {
	return float64(int(x*10+0.5)) / 10
}

func (s *Service) sourcesFor(patient string) []string {
	seen := map[string]bool{}
	for _, o := range s.store.ForPatient(patient) {
		seen[o.Source] = true
	}
	out := []string{}
	for k := range seen {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

// plan builds a FHIR bundle for pophealth: the patient's wellness
// observations as Observation resources, plus an optional synthesized
// Patient shell when the caller supplies age/sex (the SHR $everything
// merge happens at the edge — this service never fabricates clinical
// data, it only forwards its own journal).
func (s *Service) plan(patient string, age, sex string) (map[string]any, error) {
	entries := []map[string]any{}
	if age != "" {
		pat := map[string]any{
			"resourceType": "Patient", "id": patient,
			"birthDate": birthYearFromAge(age) + "-01-01",
		}
		if sex != "" {
			pat["gender"] = sex
		}
		entries = append(entries, map[string]any{"resource": pat})
	}
	for _, o := range s.store.ForPatient(patient) {
		entries = append(entries, map[string]any{"resource": o.toFHIR()})
	}
	bundle := map[string]any{
		"resourceType": "Bundle", "type": "searchset", "entry": entries,
	}
	return s.planner.Plan(patient, bundle)
}

func birthYearFromAge(age string) string {
	var a int
	fmt.Sscanf(age, "%d", &a)
	if a <= 0 || a > 120 {
		return ""
	}
	return fmt.Sprint(time.Now().Year() - a)
}

// ---------------------------------------------------------------- HTTP

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

func (s *Service) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /wellness/ingest", func(w http.ResponseWriter, r *http.Request) {
		var req IngestRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"bad json"}`, http.StatusBadRequest)
			return
		}
		res, err := s.ingest(req)
		switch {
		case errors.Is(err, ErrUnknownSource), errors.Is(err, ErrUnknownType):
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusForbidden)
		case err != nil:
			http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
		default:
			writeJSON(w, 200, res)
		}
	})
	mux.HandleFunc("GET /wellness/{patient}/summary", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, s.summary(r.PathValue("patient"), time.Now().UTC()))
	})
	mux.HandleFunc("GET /wellness/{patient}/plan", func(w http.ResponseWriter, r *http.Request) {
		plan, err := s.plan(r.PathValue("patient"),
			r.URL.Query().Get("age"), r.URL.Query().Get("sex"))
		if err != nil {
			http.Error(w, `{"error":"plan unavailable: `+err.Error()+`"}`, http.StatusBadGateway)
			return
		}
		writeJSON(w, 200, plan)
	})
	mux.HandleFunc("GET /wellness/sources", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]any{"sources": SOURCES})
	})
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Write([]byte("ok"))
	})
	return mux
}

func main() {
	dir := os.Getenv("WELLNESS_DATA_DIR")
	store, err := NewStore(dir)
	if err != nil {
		log.Fatal(err)
	}
	svc := &Service{store: store}
	if shr := os.Getenv("SHR_URL"); shr != "" {
		svc.mirror = HTTPMirror{BaseURL: shr}
		log.Printf("mirroring wellness observations into SHR at %s", shr)
	}
	svc.planner = SubprocessPlanner{
		Python: env("PYTHON", "python3"),
		Script: env("POPHEALTH", "../../packages/pophealth/pophealth.py"),
		Dir:    dir,
	}
	addr := env("LISTEN", ":8090")
	log.Printf("wellness-service on %s (sources: %d)", addr, len(SOURCES))
	log.Fatal(http.ListenAndServe(addr, svc.routes()))
}

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
