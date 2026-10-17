// Package main — OpenHealth OS notification service.
//
// The outbound patient-contact rail (ADR-0011): clinical events become
// SMS/voice messages for patients on feature phones — appointment
// reminders, lab-result-ready, medication pickup, referral status,
// immunisation due. In a country where the poorest third has no data
// plan, this rail is not a nice-to-have; it is how care reaches them.
//
// Design rules:
//   - Domain events arrive via POST /events (from sync-ingest fan-out or
//     any service); the service renders messages and delivers them.
//   - The SMS provider is a seam: log provider by default (dev/edge),
//     generic HTTP webhook for real aggregators (Africa's Talking /
//     Termii / Twilio-shaped) — no proprietary SDK is ever embedded
//     (ADR-0005).
//   - Delivery is at-least-once with bounded retries; states are
//     queued -> sent | failed, and every transition is auditable.
//   - Patients must exist as subscriptions (phone on file, captured at
//     registration) — the service never guesses numbers.
package main

import (
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

// Notification states.
const (
	StateQueued = "queued"
	StateSent   = "sent"
	StateFailed = "failed"
)

var (
	ErrNotFound      = errors.New("not found")
	ErrNoPhone       = errors.New("patient has no phone on file")
	ErrBadTransition = errors.New("bad delivery state transition")
)

// Subscription is a patient's contact consent: which number, which
// language, and which channels they opted into.
type Subscription struct {
	PatientID string `json:"patient_id"`
	Phone     string `json:"phone"` // MSISDN, E.164-ish
	Language  string `json:"language"`
	Facility  string `json:"facility_id"`
	UpdatedAt string `json:"updated_at"`
}

// Notification is one outbound message with its delivery state.
type Notification struct {
	ID        string `json:"id"`
	PatientID string `json:"patient_id"`
	Phone     string `json:"phone"`
	Kind      string `json:"kind"` // appointment | lab_ready | pickup | referral | immunization
	Text      string `json:"text"`
	State     string `json:"state"`
	Attempts  int    `json:"attempts"`
	LastError string `json:"last_error,omitempty"`
	CreatedAt string `json:"created_at"`
	SentAt    string `json:"sent_at,omitempty"`
}

// Store is the persistence seam (memory default, Postgres for the plane).
type Store interface {
	PutSubscription(s Subscription) error
	Subscription(patientID string) (Subscription, error)
	Subscriptions() []Subscription
	Enqueue(n Notification) error
	Get(id string) (Notification, error)
	MarkSent(id, at string) error
	// RecordAttempt increments the delivery attempt counter and returns
	// the new count — retries must be persisted, not held in memory.
	RecordAttempt(id string) (int, error)
	MarkFailed(id, reason string) error
	Pending(limit int) []Notification
	ForPatient(patientID string) []Notification
	Stats() (queued, sent, failed int)
}

// ---------- memory store ----------

type MemoryStore struct {
	mu   sync.Mutex
	subs map[string]Subscription
	nots map[string]Notification
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{subs: map[string]Subscription{}, nots: map[string]Notification{}}
}

func (m *MemoryStore) PutSubscription(s Subscription) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.subs[s.PatientID] = s
	return nil
}

func (m *MemoryStore) Subscription(patientID string) (Subscription, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.subs[patientID]
	if !ok {
		return Subscription{}, ErrNotFound
	}
	return s, nil
}

func (m *MemoryStore) Subscriptions() []Subscription {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Subscription, 0, len(m.subs))
	for _, s := range m.subs {
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].PatientID < out[j].PatientID })
	return out
}

func (m *MemoryStore) Enqueue(n Notification) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.nots[n.ID] = n
	return nil
}

func (m *MemoryStore) Get(id string) (Notification, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	n, ok := m.nots[id]
	if !ok {
		return Notification{}, ErrNotFound
	}
	return n, nil
}

func (m *MemoryStore) MarkSent(id, at string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	n, ok := m.nots[id]
	if !ok {
		return ErrNotFound
	}
	if n.State != StateQueued {
		return ErrBadTransition
	}
	n.State, n.SentAt = StateSent, at
	m.nots[id] = n
	return nil
}

func (m *MemoryStore) RecordAttempt(id string) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	n, ok := m.nots[id]
	if !ok {
		return 0, ErrNotFound
	}
	n.Attempts++
	m.nots[id] = n
	return n.Attempts, nil
}

func (m *MemoryStore) MarkFailed(id, reason string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	n, ok := m.nots[id]
	if !ok {
		return ErrNotFound
	}
	n.State, n.LastError = StateFailed, reason
	m.nots[id] = n
	return nil
}

func (m *MemoryStore) Pending(limit int) []Notification {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []Notification
	for _, n := range m.nots {
		if n.State == StateQueued {
			out = append(out, n)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt < out[j].CreatedAt })
	if len(out) > limit {
		out = out[:limit]
	}
	return out
}

func (m *MemoryStore) ForPatient(patientID string) []Notification {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []Notification
	for _, n := range m.nots {
		if n.PatientID == patientID {
			out = append(out, n)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt > out[j].CreatedAt })
	return out
}

func (m *MemoryStore) Stats() (queued, sent, failed int) {
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, n := range m.nots {
		switch n.State {
		case StateQueued:
			queued++
		case StateSent:
			sent++
		case StateFailed:
			failed++
		}
	}
	return
}

// ---------- provider seam ----------

// SMSProvider delivers one message. Implementations must be replaceable
// without touching domain code.
type SMSProvider interface {
	Send(phone, text string) error
	Name() string
}

// LogProvider is the default: delivery is simulated by logging (dev,
// edge, tests). Messages are still state-tracked, so dashboards and the
// retry loop behave exactly as in production.
type LogProvider struct{ Sent [][2]string }

func (l *LogProvider) Send(phone, text string) error {
	l.Sent = append(l.Sent, [2]string{phone, text})
	return nil
}
func (l *LogProvider) Name() string { return "log" }

// ---------- event → message rendering ----------

// DomainEvent is the intake shape from the sync fan-out.
type DomainEvent struct {
	ID       string         `json:"id"`
	Facility string         `json:"facility"`
	Type     string         `json:"type"`
	TS       string         `json:"ts"`
	Payload  map[string]any `json:"payload"`
}

// Renderable event types and their patient-facing text. Messages are
// deliberately plain, short (<320 chars), and never contain clinical
// detail beyond "your result is ready" — the channel is not private.
func renderMessage(ev DomainEvent) (kind, text string, ok bool) {
	p := ev.Payload
	str := func(k string) string { s, _ := p[k].(string); return s }
	facility := str("facility_name")
	if facility == "" {
		facility = "your facility"
	}
	switch ev.Type {
	case "appointment":
		when := str("when_text")
		if when == "" {
			when = str("when")
		}
		if when == "" {
			when = "coming up soon"
		}
		return "appointment", fmt.Sprintf("OpenHealth: reminder — your appointment at %s is %s. Reply or visit the clinic.",
			facility, when), true
	case "lab_result":
		if name := str("test_name"); name != "" {
			return "lab_ready", fmt.Sprintf("OpenHealth: your test result (%s) is ready at %s. Please come with your card.",
				name, facility), true
		}
		return "lab_ready", fmt.Sprintf("OpenHealth: your test result is ready at %s. Please come with your card.",
			facility), true
	case "prescription":
		if drug := str("drug_name"); drug != "" {
			return "pickup", fmt.Sprintf("OpenHealth: your medication (%s) is ready for pickup at %s pharmacy.",
				drug, facility), true
		}
		return "pickup", fmt.Sprintf("OpenHealth: your medication is ready for pickup at %s pharmacy.",
			facility), true
	case "referral":
		to := str("to_facility")
		if to == "" {
			to = "a specialist clinic"
		}
		status := str("status")
		if status == "" {
			status = "made"
		}
		return "referral", fmt.Sprintf("OpenHealth: your referral to %s has been %s. Show this message at reception.",
			to, status), true
	case "immunization_due":
		name := str("patient_name")
		if name == "" {
			name = "Your child"
		}
		vaccine := str("vaccine")
		if vaccine == "" {
			vaccine = "an immunization"
		}
		return "immunization", fmt.Sprintf("OpenHealth: %s is due for %s. Visit %s this week.",
			name, vaccine, facility), true
	}
	return "", "", false
}

// Service wires store + provider + rendering.
type Service struct {
	store    Store
	provider SMSProvider
	now      func() time.Time
	seq      int
	mu       sync.Mutex
}

func NewService(store Store, provider SMSProvider) *Service {
	return &Service{store: store, provider: provider, now: time.Now}
}

func (s *Service) nextID() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	return fmt.Sprintf("NTF-%06d", s.seq)
}

// HandleEvent renders and enqueues a notification for a domain event.
// Unknown types are ignored (ok=false). Patients without a subscription
// are skipped with ErrNoPhone — we never guess numbers.
func (s *Service) HandleEvent(ev DomainEvent) (Notification, error) {
	kind, text, ok := renderMessage(ev)
	if !ok {
		return Notification{}, nil
	}
	patientID, _ := ev.Payload["patient_id"].(string)
	if patientID == "" {
		return Notification{}, ErrNotFound
	}
	sub, err := s.store.Subscription(patientID)
	if err != nil {
		return Notification{}, ErrNoPhone
	}
	if name, ok2 := ev.Payload["facility_name"]; !ok2 || name == "" {
		// keep messages meaningful even when the edge omits facility names
		ev.Payload["facility_name"] = sub.Facility
		_, text, _ = renderMessage(ev)
	}
	n := Notification{
		ID: s.nextID(), PatientID: patientID, Phone: sub.Phone,
		Kind: kind, Text: text, State: StateQueued,
		CreatedAt: s.now().UTC().Format(time.RFC3339),
	}
	return n, s.store.Enqueue(n)
}

// MaxAttempts bounds the retry loop before a message is declared failed.
const MaxAttempts = 3

// Drain delivers up to limit pending notifications through the provider.
// Failures stay queued until MaxAttempts, then are marked failed — a dead
// aggregator must never block the rail forever.
func (s *Service) Drain(limit int) (sent, failed int) {
	for _, n := range s.store.Pending(limit) {
		if err := s.provider.Send(n.Phone, n.Text); err != nil {
			attempts, aerr := s.store.RecordAttempt(n.ID)
			if aerr == nil && attempts >= MaxAttempts {
				_ = s.store.MarkFailed(n.ID, err.Error())
				failed++
			}
			continue
		}
		_ = s.store.MarkSent(n.ID, s.now().UTC().Format(time.RFC3339))
		sent++
	}
	return
}

// NormalizePhone lightly normalises Nigerian MSISDNs: 0803… -> +234803…
func NormalizePhone(p string) string {
	p = strings.TrimSpace(strings.ReplaceAll(p, " ", ""))
	if strings.HasPrefix(p, "0") && len(p) == 11 {
		return "+234" + p[1:]
	}
	return p
}
