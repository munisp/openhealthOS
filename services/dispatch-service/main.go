// Package main — OpenHealth OS emergency dispatch service (ADR-0019).
//
// Africa's missing piece is not the ambulance — it is the dispatcher:
// one reachable number (WhatsApp, SMS, USSD, the patient app's SOS
// button) that turns "something is wrong at this place" into a triaged,
// facility-assigned, auditable incident. This service is that
// dispatcher.
//
// What it does:
//   - intake on every channel a Nigerian actually has: WhatsApp Cloud
//     API webhook (the one number), plain SOS POST (USSD/SMS gateways
//     and the app), with geolocation when the channel carries it;
//   - deterministic triage (red/orange/green) from a keyword protocol
//     — data, overridable by DISPATCH_TRIAGE_FILE, never a black box;
//   - nearest-appropriate-facility assignment over the registry's
//     haversine /facilities/near (emergency amenity first, then
//     hospital level, then anything), with the answer and the distance
//     recorded on the incident;
//   - the incident lifecycle: received → triaged → dispatched →
//     acknowledged → on_scene → transported → closed (| cancelled),
//     every transition attributed and timestamped;
//   - repeat-SOS dedupe: a panicked caller mashing redial inside the
//     dedupe window updates the same incident, never spawns clones.
//
// What it is NOT: ambulance fleet telemetry or a voice call centre.
// Vehicles are a facility concern; this is the national intake and
// assignment spine. The domain event stream (NOTIFY_URL) is how the
// caller and the facility learn what happened — the dispatcher never
// goes silent.
package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// Incident statuses and the legal transitions.
const (
	StReceived    = "received"
	StTriaged     = "triaged"
	StDispatched  = "dispatched"
	StAcked       = "acknowledged"
	StOnScene     = "on_scene"
	StTransported = "transported"
	StClosed      = "closed"
	StCancelled   = "cancelled"
)

var transitions = map[string][]string{
	StReceived:    {StTriaged, StCancelled},
	StTriaged:     {StDispatched, StCancelled},
	StDispatched:  {StAcked, StCancelled},
	StAcked:       {StOnScene, StCancelled},
	StOnScene:     {StTransported, StClosed, StCancelled},
	StTransported: {StClosed},
}

// Triage categories.
const (
	TriageRed    = "red"    // life threat — minutes matter
	TriageOrange = "orange" // urgent — facility today
	TriageGreen  = "green"  // stable — advice / self-care guidance
)

var (
	ErrNotFound      = errors.New("not found")
	ErrBadTransition = errors.New("bad status transition")
	ErrValidation    = errors.New("validation")
)

// TimelineEntry is one attributed, timestamped step of the incident.
type TimelineEntry struct {
	TS     string `json:"ts"`
	Actor  string `json:"actor"` // service | dispatcher id | facility id | paramedic id
	Action string `json:"action"`
	Detail string `json:"detail,omitempty"`
}

// Incident is one emergency from first contact to closure.
type Incident struct {
	ID           string          `json:"id"`
	Channel      string          `json:"channel"` // whatsapp | sms | ussd | app | voice
	CallerPhone  string          `json:"caller_phone"`
	PatientID    string          `json:"patient_id,omitempty"`
	Lat          float64         `json:"lat,omitempty"`
	Lng          float64         `json:"lng,omitempty"`
	LocSource    string          `json:"loc_source,omitempty"` // whatsapp-pin | app-gps | cell-tower | none
	Complaint    string          `json:"complaint"`
	Triage       string          `json:"triage"`
	TriageReason string          `json:"triage_reason"`
	Status       string          `json:"status"`
	FacilityID   string          `json:"facility_id,omitempty"`
	FacilityName string          `json:"facility_name,omitempty"`
	FacilityKm   float64         `json:"facility_km,omitempty"`
	UnitID       string          `json:"unit_id,omitempty"` // responding ambulance (ADR-0021)
	Timeline     []TimelineEntry `json:"timeline"`
	CreatedAt    string          `json:"created_at"`
	UpdatedAt    string          `json:"updated_at"`
}

// triageRule maps keywords to a category — the protocol is data.
type triageRule struct {
	Category string   `json:"category"`
	Reason   string   `json:"reason"`
	Keywords []string `json:"keywords"`
}

// defaultTriageProtocol: red flags first. Nigerian emergency reality —
// obstetric haemorrhage, convulsions, breathing, unconsciousness —
// before trauma phrasing.
var defaultTriageProtocol = []triageRule{
	{TriageRed, "possible life threat: bleeding", []string{"bleeding", "blood", "haemorrhage"}},
	{TriageRed, "possible life threat: unconscious", []string{"unconscious", "not responding", "unresponsive", "collapsed"}},
	{TriageRed, "possible life threat: breathing", []string{"breathing", "breath", "suffocat", "choking"}},
	{TriageRed, "possible life threat: chest pain", []string{"chest pain", "heart"}},
	{TriageRed, "possible life threat: convulsion", []string{"convuls", "seizure", "fitting"}},
	{TriageRed, "possible life threat: stroke signs", []string{"stroke", "one side", "slurred"}},
	{TriageRed, "obstetric emergency", []string{"labour", "labor", "baby is coming", "cord"}},
	{TriageOrange, "urgent: trauma", []string{"accident", "fracture", "broken", "fell", "fall", "burn"}},
	{TriageOrange, "urgent: severe infection signs", []string{"fever", "vomiting", "diarrhoea", "diarrhea", "stooling"}},
	{TriageOrange, "urgent: pregnancy concern", []string{"pregnant", "pregnancy"}},
}

func loadTriageProtocol(getenv func(string) string) []triageRule {
	if p := getenv("DISPATCH_TRIAGE_FILE"); p != "" {
		raw, err := os.ReadFile(p)
		if err != nil {
			panic("dispatch triage file: " + err.Error())
		}
		var rules []triageRule
		if err := json.Unmarshal(raw, &rules); err != nil {
			panic("dispatch triage file: " + err.Error())
		}
		if len(rules) == 0 {
			panic("dispatch triage file: no rules")
		}
		return rules
	}
	return defaultTriageProtocol
}

// triage runs the protocol in order; first match wins.
func triage(protocol []triageRule, complaint string) (category, reason string) {
	low := strings.ToLower(complaint)
	for _, r := range protocol {
		for _, kw := range r.Keywords {
			if strings.Contains(low, kw) {
				return r.Category, r.Reason
			}
		}
	}
	return TriageGreen, "no red/orange flag in complaint"
}

// ---------------------------------------------------------------- store

type Store interface {
	Put(i Incident) error
	Get(id string) (Incident, error)
	List(status, facilityID string) []Incident
	// OpenForCaller returns the caller's non-closed incident inside the
	// dedupe window, if any — the repeat-SOS gate.
	OpenForCaller(phone string, since time.Time) (Incident, error)
	NextID() string
}

type MemoryStore struct {
	mu   sync.Mutex
	rows map[string]Incident
	seq  int
}

func NewMemoryStore() *MemoryStore { return &MemoryStore{rows: map[string]Incident{}} }

func (m *MemoryStore) NextID() string {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.seq++
	return fmt.Sprintf("INC-%05d", m.seq)
}

func (m *MemoryStore) Put(i Incident) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.rows[i.ID] = i
	return nil
}

func (m *MemoryStore) Get(id string) (Incident, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if i, ok := m.rows[id]; ok {
		return i, nil
	}
	return Incident{}, ErrNotFound
}

func (m *MemoryStore) List(status, facilityID string) []Incident {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Incident{}
	for _, i := range m.rows {
		if status != "" && i.Status != status {
			continue
		}
		if facilityID != "" && i.FacilityID != facilityID {
			continue
		}
		out = append(out, i)
	}
	sort.Slice(out, func(a, b int) bool { return out[a].CreatedAt < out[b].CreatedAt })
	return out
}

func (m *MemoryStore) OpenForCaller(phone string, since time.Time) (Incident, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	best := Incident{}
	for _, i := range m.rows {
		if i.CallerPhone != phone || i.Status == StClosed || i.Status == StCancelled {
			continue
		}
		created, err := time.Parse(time.RFC3339, i.CreatedAt)
		if err != nil || created.Before(since) {
			continue
		}
		if best.ID == "" || i.CreatedAt > best.CreatedAt {
			best = i
		}
	}
	if best.ID == "" {
		return Incident{}, ErrNotFound
	}
	return best, nil
}
