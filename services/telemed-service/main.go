// Telemedicine service (ADR-0026): async-first consults with voice/video
// escalation onto the Phase-2 LiveKit fabric.
//
// Doctrine: video is the LAST tier. Most rural demand is met async
// (structured history + photos, doctor replies within hours — works on 2G);
// voice rooms cover escalation on 3G; video only where bandwidth allows.
// Every consult is consented, documented, and quality-measurable — that is
// the platform's advantage over a WhatsApp call, not the pixels.
package main

import (
	"errors"
	"fmt"
	"sort"
	"time"
)

// Consult states. Async by default; escalation moves tier, never state.
const (
	StSubmitted   = "submitted"
	StTriaged     = "triaged"
	StInReview    = "in_review"
	StAdviceGiven = "advice_given"
	StCompleted   = "completed"
	StEscalated   = "escalated_to_call"
	StCancelled   = "cancelled"
)

// Tiers: async (2G), voice room (3G), video room (decent 3G+/4G).
const (
	TierAsync = "async"
	TierVoice = "voice"
	TierVideo = "video"
)

// Advice SLA: a submitted consult should have clinician advice within 4h
// (routine) or 1h (urgent). Breaches surface on the overdue list.
var adviceSLA = map[string]time.Duration{
	"routine": 4 * time.Hour,
	"urgent":  1 * time.Hour,
}

var consultTransitions = map[string][]string{
	StSubmitted:   {StTriaged, StCancelled},
	StTriaged:     {StInReview, StCancelled},
	StInReview:    {StAdviceGiven, StEscalated, StCancelled},
	StAdviceGiven: {StInReview, StCompleted, StEscalated, StCancelled},
	StEscalated:   {StInReview, StCompleted}, // the call ends, back to thread
	StCompleted:   {},
	StCancelled:   {},
}

type Message struct {
	At     time.Time `json:"at"`
	Author string    `json:"author"`
	Role   string    `json:"role"` // patient | chw | clinician | system
	Body   string    `json:"body"`
	// Media are references (uploaded blobs live in object storage); the
	// thread stores pointers + a note, never bytes — records stay light.
	MediaRef string `json:"media_ref,omitempty"`
}

type Consult struct {
	ID         string    `json:"id"`
	PatientID  string    `json:"patient_id"`
	FacilityID string    `json:"facility_id"`
	State      string    `json:"state"`
	Tier       string    `json:"tier"` // current tier; starts async
	Priority   string    `json:"priority"`
	Complaint  string    `json:"complaint"`
	Consent    bool      `json:"consent"` // NDPA: tele-consent recorded at intake
	Clinician  string    `json:"clinician,omitempty"`
	Room       string    `json:"room,omitempty"` // LiveKit room when escalated
	Messages   []Message `json:"messages"`
	CreatedAt  time.Time `json:"created_at"`
	UpdatedAt  time.Time `json:"updated_at"`
}

var ErrTransition = errors.New("illegal consult transition")

type Store interface {
	Create(c Consult) (Consult, error)
	Get(id string) (Consult, error)
	Update(c Consult) error
	List(state string) []Consult
}

type MemoryStore struct {
	seq      int
	consults map[string]Consult
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{consults: map[string]Consult{}}
}

func (m *MemoryStore) Create(c Consult) (Consult, error) {
	if !c.Consent {
		return Consult{}, errors.New("tele-consult requires recorded consent (NDPA)")
	}
	m.seq++
	c.ID = fmt.Sprintf("TC-%05d", m.seq)
	if c.State == "" {
		c.State = StSubmitted
	}
	if c.Tier == "" {
		c.Tier = TierAsync
	}
	if c.Priority == "" {
		c.Priority = "routine"
	}
	now := time.Now()
	c.CreatedAt, c.UpdatedAt = now, now
	m.consults[c.ID] = c
	return c, nil
}

func (m *MemoryStore) Get(id string) (Consult, error) {
	c, ok := m.consults[id]
	if !ok {
		return Consult{}, fmt.Errorf("unknown consult: %s", id)
	}
	return c, nil
}

func (m *MemoryStore) Update(c Consult) error {
	if _, ok := m.consults[c.ID]; !ok {
		return fmt.Errorf("unknown consult: %s", c.ID)
	}
	c.UpdatedAt = time.Now()
	m.consults[c.ID] = c
	return nil
}

func (m *MemoryStore) List(state string) []Consult {
	out := []Consult{}
	for _, c := range m.consults {
		if state == "" || c.State == state {
			out = append(out, c)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Transition validates and moves a consult. `escalated_to_call` requires a
// room to have been provisioned first — a call with no room is a bug.
func Transition(c Consult, to string) (Consult, error) {
	for _, allowed := range consultTransitions[c.State] {
		if allowed == to {
			if to == StEscalated && c.Room == "" {
				return c, errors.New("escalation requires a provisioned room")
			}
			c.State = to
			return c, nil
		}
	}
	return c, fmt.Errorf("%w: %s -> %s", ErrTransition, c.State, to)
}

// AdviceOverdue lists consults with no clinician message inside the SLA.
func AdviceOverdue(consults []Consult, now time.Time) []Consult {
	out := []Consult{}
	for _, c := range consults {
		if c.State != StSubmitted && c.State != StTriaged && c.State != StInReview {
			continue
		}
		advised := false
		for _, m := range c.Messages {
			if m.Role == "clinician" {
				advised = true
				break
			}
		}
		if !advised && now.Sub(c.CreatedAt) > adviceSLA[c.Priority] {
			out = append(out, c)
		}
	}
	return out
}
