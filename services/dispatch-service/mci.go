package main

// mci.go — mass-casualty incident mode (ADR-0022).
//
// A bus crash is not one caller; it is thirty patients at one
// location. Regular dispatch triages one complaint. MCI mode instead
// tracks a casualty list with START triage tags (red immediate,
// yellow delayed, green minor, black expectant/deceased), counts the
// load per tag so command can see the shape of the incident at a
// glance, and computes a surge view: which facilities near the scene
// have beds, so transport is distributed instead of overwhelming the
// nearest hospital while the next one sits empty.
//
// An MCI is a parent incident (channel "mci-command") with a casualty
// list; each transported casualty can be linked to a receiving
// facility. Deterministic, offline, stdlib — same doctrine as the rest
// of the service.

import (
	"fmt"
	"sort"
	"sync"
	"time"
)

// START triage tags.
const (
	TagRed    = "red"    // immediate
	TagYellow = "yellow" // delayed
	TagGreen  = "green"  // minor (walking wounded)
	TagBlack  = "black"  // expectant / deceased
)

var tagOrder = []string{TagRed, TagYellow, TagGreen, TagBlack}

// Casualty transport states.
const (
	CasualtyAtScene     = "at_scene"
	CasualtyTransported = "transported"
	CasualtyReceived    = "received"
)

type Casualty struct {
	ID          string `json:"id"`
	Tag         string `json:"tag"`         // START color
	AgeGroup    string `json:"age_group"`   // adult | child | infant
	Description string `json:"description"` // "adult male, femur fracture"
	Status      string `json:"status"`      // at_scene | transported | received
	FacilityID  string `json:"facility_id,omitempty"`
	UpdatedAt   string `json:"updated_at"`
}

type MCI struct {
	ID         string `json:"id"`
	IncidentID string `json:"incident_id"` // parent dispatch incident
	Name       string `json:"name"`        // "Lagos-Ibadan expressway bus crash"
	Lat, Lng   float64
	CreatedAt  string     `json:"created_at"`
	Casualties []Casualty `json:"casualties"`
}

// Counts is the incident commander's shape-of-the-scene.
func (m MCI) Counts() map[string]int {
	out := map[string]int{}
	for _, t := range tagOrder {
		out[t] = 0
	}
	for _, c := range m.Casualties {
		if _, ok := out[c.Tag]; ok {
			out[c.Tag]++
		}
	}
	out["total"] = len(m.Casualties)
	atScene := 0
	for _, c := range m.Casualties {
		if c.Status == CasualtyAtScene {
			atScene++
			out[c.Tag+"_at_scene"]++
		}
	}
	out["at_scene"] = atScene
	return out
}

type MCIStore struct {
	mu   sync.Mutex
	rows map[string]MCI
	seq  int
	cseq int
}

func NewMCIStore() *MCIStore { return &MCIStore{rows: map[string]MCI{}} }

func (s *MCIStore) Create(incidentID, name string, lat, lng float64, now time.Time) (MCI, error) {
	if name == "" {
		return MCI{}, fmt.Errorf("%w: name required", ErrValidation)
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	m := MCI{
		ID: fmt.Sprintf("MCI-%04d", s.seq), IncidentID: incidentID, Name: name,
		Lat: lat, Lng: lng, CreatedAt: now.UTC().Format(time.RFC3339),
	}
	s.rows[m.ID] = m
	return m, nil
}

func (s *MCIStore) Get(id string) (MCI, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if m, ok := s.rows[id]; ok {
		return m, nil
	}
	return MCI{}, ErrNotFound
}

func (s *MCIStore) List() []MCI {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := []MCI{}
	for _, m := range s.rows {
		out = append(out, m)
	}
	sort.Slice(out, func(a, b int) bool { return out[a].ID < out[b].ID })
	return out
}

func validTag(tag string) bool {
	for _, t := range tagOrder {
		if t == tag {
			return true
		}
	}
	return false
}

func (s *MCIStore) AddCasualty(mciID, tag, ageGroup, description string, now time.Time) (MCI, error) {
	if !validTag(tag) {
		return MCI{}, fmt.Errorf("%w: tag must be one of %v", ErrValidation, tagOrder)
	}
	if ageGroup == "" {
		ageGroup = "adult"
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	m, ok := s.rows[mciID]
	if !ok {
		return MCI{}, ErrNotFound
	}
	s.cseq++
	m.Casualties = append(m.Casualties, Casualty{
		ID: fmt.Sprintf("CAS-%05d", s.cseq), Tag: tag, AgeGroup: ageGroup,
		Description: description, Status: CasualtyAtScene,
		UpdatedAt: now.UTC().Format(time.RFC3339),
	})
	s.rows[mciID] = m
	return m, nil
}

func (s *MCIStore) UpdateCasualty(mciID, casualtyID, tag, status, facilityID string, now time.Time) (MCI, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	m, ok := s.rows[mciID]
	if !ok {
		return MCI{}, ErrNotFound
	}
	for i := range m.Casualties {
		if m.Casualties[i].ID != casualtyID {
			continue
		}
		if tag != "" {
			if !validTag(tag) {
				return MCI{}, fmt.Errorf("%w: tag must be one of %v", ErrValidation, tagOrder)
			}
			m.Casualties[i].Tag = tag
		}
		if status != "" {
			m.Casualties[i].Status = status
		}
		if facilityID != "" {
			m.Casualties[i].FacilityID = facilityID
		}
		m.Casualties[i].UpdatedAt = now.UTC().Format(time.RFC3339)
		s.rows[mciID] = m
		return m, nil
	}
	return MCI{}, ErrNotFound
}
