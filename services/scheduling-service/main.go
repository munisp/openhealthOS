// scheduling-service — the appointment engine (ADR-0017).
//
// What it is: provider/room/equipment schedules expressed as weekly
// templates, materialised into bookable slots; patient bookings with
// atomic no-double-book guarantees; reschedule/cancel with waitlist
// promotion; check-in → complete/no-show day flow. The same engine
// books a nurse consult at a PHC and a theatre room at a general
// hospital — resource_type is the only difference.
//
// What it is not: a billing or rostering system. Staff rosters are
// approximated by provider schedules (a provider without a schedule has
// no slots — that IS the roster at PHC scale).
//
// Doctrine: care is never blocked by the engine — an overbooked clinic
// is a queue problem, so waitlisting is first-class. Coverage checks at
// booking are advisory snapshots (eligibility-service), never a gate.
package main

import (
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"
)

var (
	ErrNotFound      = errors.New("not found")
	ErrSlotFull      = errors.New("slot is full — join the waitlist")
	ErrSlotPast      = errors.New("slot is not bookable (past or beyond the booking window)")
	ErrBadTransition = errors.New("invalid appointment state transition")
	ErrBadSchedule   = errors.New("invalid schedule template")
)

// BookingWindowDays is how far ahead a patient may book. PHC reality:
// further out than a month, no-shows dominate.
const BookingWindowDays = 30

// Schedule is a weekly template: "Dr. Musa, general consults, Mondays
// 08:00–12:00, 20-minute slots, 2 patients per slot".
type Schedule struct {
	ID           string `json:"id"`
	FacilityID   string `json:"facility_id"`
	ResourceID   string `json:"resource_id"`   // provider/room/equipment id
	ResourceName string `json:"resource_name"` // display: "Nurse Amina" / "Theatre 1"
	ResourceType string `json:"resource_type"` // provider | room | equipment
	Service      string `json:"service"`       // general | anc | immunization | dental | theatre ...
	Weekday      int    `json:"weekday"`       // 0=Sunday … 6=Saturday
	StartMin     int    `json:"start_min"`     // minutes from midnight
	EndMin       int    `json:"end_min"`
	SlotMinutes  int    `json:"slot_minutes"`
	Capacity     int    `json:"capacity"` // patients per slot (PHC overbooking is real)
	Active       bool   `json:"active"`
}

// Slot is one materialised, bookable interval.
type Slot struct {
	ID           string `json:"id"` // scheduleID:date:startMin — deterministic, idempotent
	ScheduleID   string `json:"schedule_id"`
	FacilityID   string `json:"facility_id"`
	Service      string `json:"service"`
	ResourceName string `json:"resource_name"`
	Start        string `json:"start"` // RFC3339
	End          string `json:"end"`
	Capacity     int    `json:"capacity"`
	Booked       int    `json:"booked"`
}

func (s Slot) Free() int { return s.Capacity - s.Booked }

// Appointment is one patient's hold on one slot.
type Appointment struct {
	ID           string         `json:"id"`
	SlotID       string         `json:"slot_id"`
	PatientID    string         `json:"patient_id"`
	FacilityID   string         `json:"facility_id"`
	Service      string         `json:"service"`
	ResourceName string         `json:"resource_name"`
	Start        string         `json:"start"`
	End          string         `json:"end"`
	Status       string         `json:"status"` // booked | checked-in | completed | cancelled | no-show
	Reason       string         `json:"reason,omitempty"`
	Coverage     map[string]any `json:"coverage,omitempty"` // advisory snapshot, never a gate
	CreatedAt    string         `json:"created_at"`
}

// WaitlistEntry is a patient's place in line for a service at a
// facility; promoted FIFO into the next slot that frees up.
type WaitlistEntry struct {
	ID         string `json:"id"`
	PatientID  string `json:"patient_id"`
	FacilityID string `json:"facility_id"`
	Service    string `json:"service"`
	CreatedAt  string `json:"created_at"`
}

// ---------------------------------------------------------------- store

// Store is the persistence seam. Memory for edge/dev/tests; Postgres on
// the state plane. Booking invariants (no double-book, atomic
// reschedule, FIFO promotion) are enforced inside one lock/transaction
// — never split across calls.
type Store interface {
	PutSchedule(s Schedule) (Schedule, error)
	Schedules(facilityID string) []Schedule
	Generate(scheduleID, date string, now time.Time) ([]Slot, error)
	Slots(facilityID, service, from, to string) []Slot
	Book(slotID, patientID, reason string, coverage map[string]any, now time.Time) (Appointment, error)
	Cancel(appointmentID string, now time.Time) (Appointment, *Appointment, error) // cancelled, promoted
	Reschedule(appointmentID, newSlotID string, now time.Time) (Appointment, error)
	Transition(appointmentID, action string) (Appointment, error) // checkin | complete | noshow
	Appointment(id string) (Appointment, error)
	PatientAppointments(patientID string) []Appointment
	DayList(facilityID, date string) []Appointment
	Waitlist(patientID, facilityID, service string, now time.Time) (WaitlistEntry, error)
	WaitlistFor(facilityID, service string) []WaitlistEntry
	// FacilityIDs enumerates facilities that have schedules (recommender).
	FacilityIDs() []string
}

// ---------------------------------------------------------------- memory

type MemoryStore struct {
	mu        sync.Mutex
	seq       int
	schedules map[string]*Schedule
	slots     map[string]*Slot
	appts     map[string]*Appointment
	waitlist  map[string]*WaitlistEntry
}

func NewMemoryStore() *MemoryStore {
	return &MemoryStore{
		schedules: map[string]*Schedule{},
		slots:     map[string]*Slot{},
		appts:     map[string]*Appointment{},
		waitlist:  map[string]*WaitlistEntry{},
	}
}

func (m *MemoryStore) nextID(prefix string) string {
	m.seq++
	return fmt.Sprintf("%s-%05d", prefix, m.seq)
}

func validSchedule(s Schedule) error {
	if s.FacilityID == "" || s.ResourceID == "" || s.Service == "" {
		return ErrBadSchedule
	}
	if s.Weekday < 0 || s.Weekday > 6 || s.StartMin < 0 || s.EndMin > 24*60 ||
		s.StartMin >= s.EndMin || s.SlotMinutes <= 0 || s.Capacity <= 0 {
		return ErrBadSchedule
	}
	switch s.ResourceType {
	case "provider", "room", "equipment":
	default:
		return ErrBadSchedule
	}
	return nil
}

func (m *MemoryStore) PutSchedule(s Schedule) (Schedule, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := validSchedule(s); err != nil {
		return Schedule{}, err
	}
	if s.ID == "" {
		s.ID = m.nextID("SCH")
	}
	s.Active = true
	cp := s
	m.schedules[s.ID] = &cp
	return s, nil
}

func (m *MemoryStore) Schedules(facilityID string) []Schedule {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Schedule{}
	for _, s := range m.schedules {
		if s.FacilityID == facilityID && s.Active {
			out = append(out, *s)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out
}

// Generate materialises one schedule's slots for one date (YYYY-MM-DD).
// Idempotent: the deterministic slot ID means regeneration is a no-op.
func generateSlots(s *Schedule, date string) ([]Slot, error) {
	day, err := time.Parse("2006-01-02", date)
	if err != nil {
		return nil, ErrBadSchedule
	}
	if int(day.Weekday()) != s.Weekday {
		return []Slot{}, nil
	}
	out := []Slot{}
	for start := s.StartMin; start+s.SlotMinutes <= s.EndMin; start += s.SlotMinutes {
		st := day.Add(time.Duration(start) * time.Minute).UTC()
		en := st.Add(time.Duration(s.SlotMinutes) * time.Minute)
		out = append(out, Slot{
			ID:           fmt.Sprintf("%s:%s:%d", s.ID, date, start),
			ScheduleID:   s.ID,
			FacilityID:   s.FacilityID,
			Service:      s.Service,
			ResourceName: s.ResourceName,
			Start:        st.Format(time.RFC3339),
			End:          en.Format(time.RFC3339),
			Capacity:     s.Capacity,
		})
	}
	return out, nil
}

func (m *MemoryStore) Generate(scheduleID, date string, now time.Time) ([]Slot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.schedules[scheduleID]
	if !ok || !s.Active {
		return nil, ErrNotFound
	}
	slots, err := generateSlots(s, date)
	if err != nil {
		return nil, err
	}
	out := []Slot{}
	for _, sl := range slots {
		if ex, ok := m.slots[sl.ID]; ok {
			out = append(out, *ex) // keep existing booking counts
			continue
		}
		cp := sl
		m.slots[sl.ID] = &cp
		out = append(out, sl)
	}
	return out, nil
}

func (m *MemoryStore) Slots(facilityID, service, from, to string) []Slot {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Slot{}
	for _, s := range m.slots {
		if s.FacilityID != facilityID {
			continue
		}
		if service != "" && s.Service != service {
			continue
		}
		if from != "" && s.Start < from {
			continue
		}
		if to != "" && s.Start > to {
			continue
		}
		out = append(out, *s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Start < out[j].Start })
	return out
}

// bookable enforces the booking window: not in the past, not beyond
// BookingWindowDays ahead.

// FacilityIDs lists every facility with at least one schedule.
func (m *MemoryStore) FacilityIDs() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	seen := map[string]bool{}
	var out []string
	for _, s := range m.schedules {
		if !seen[s.FacilityID] {
			seen[s.FacilityID] = true
			out = append(out, s.FacilityID)
		}
	}
	sort.Strings(out)
	return out
}

func bookable(sl *Slot, now time.Time) error {
	st, err := time.Parse(time.RFC3339, sl.Start)
	if err != nil {
		return ErrSlotPast
	}
	if st.Before(now) || st.After(now.Add(BookingWindowDays*24*time.Hour)) {
		return ErrSlotPast
	}
	if sl.Free() <= 0 {
		return ErrSlotFull
	}
	return nil
}

func (m *MemoryStore) Book(slotID, patientID, reason string, coverage map[string]any, now time.Time) (Appointment, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	sl, ok := m.slots[slotID]
	if !ok {
		return Appointment{}, ErrNotFound
	}
	if err := bookable(sl, now); err != nil {
		return Appointment{}, err
	}
	sl.Booked++
	a := &Appointment{
		ID: m.nextID("APPT"), SlotID: slotID, PatientID: patientID,
		FacilityID: sl.FacilityID, Service: sl.Service, ResourceName: sl.ResourceName,
		Start: sl.Start, End: sl.End, Status: "booked", Reason: reason,
		Coverage: coverage, CreatedAt: now.UTC().Format(time.RFC3339),
	}
	m.appts[a.ID] = a
	return *a, nil
}

func (m *MemoryStore) Cancel(appointmentID string, now time.Time) (Appointment, *Appointment, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.appts[appointmentID]
	if !ok {
		return Appointment{}, nil, ErrNotFound
	}
	if a.Status != "booked" {
		return Appointment{}, nil, ErrBadTransition
	}
	a.Status = "cancelled"
	if sl, ok := m.slots[a.SlotID]; ok && sl.Booked > 0 {
		sl.Booked--
	}
	// FIFO waitlist promotion into the freed slot.
	promoted, err := m.promoteLocked(a.FacilityID, a.Service, a.SlotID, now)
	if err != nil {
		promoted = nil // an unpromotable waitlist is not an error
	}
	return *a, promoted, nil
}

// promoteLocked books the oldest matching waitlist entry into slotID if
// it fits. Caller holds the lock.
func (m *MemoryStore) promoteLocked(facilityID, service, slotID string, now time.Time) (*Appointment, error) {
	sl, ok := m.slots[slotID]
	if !ok || sl.Free() <= 0 {
		return nil, ErrSlotFull
	}
	var oldest *WaitlistEntry
	for _, w := range m.waitlist {
		if w.FacilityID == facilityID && w.Service == service {
			if oldest == nil || w.CreatedAt < oldest.CreatedAt ||
				(w.CreatedAt == oldest.CreatedAt && w.ID < oldest.ID) {
				oldest = w
			}
		}
	}
	if oldest == nil {
		return nil, ErrNotFound
	}
	sl.Booked++
	a := &Appointment{
		ID: m.nextID("APPT"), SlotID: slotID, PatientID: oldest.PatientID,
		FacilityID: sl.FacilityID, Service: sl.Service, ResourceName: sl.ResourceName,
		Start: sl.Start, End: sl.End, Status: "booked",
		CreatedAt: now.UTC().Format(time.RFC3339),
	}
	m.appts[a.ID] = a
	delete(m.waitlist, oldest.ID)
	return a, nil
}

func (m *MemoryStore) Reschedule(appointmentID, newSlotID string, now time.Time) (Appointment, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.appts[appointmentID]
	if !ok {
		return Appointment{}, ErrNotFound
	}
	if a.Status != "booked" {
		return Appointment{}, ErrBadTransition
	}
	nl, ok := m.slots[newSlotID]
	if !ok {
		return Appointment{}, ErrNotFound
	}
	if err := bookable(nl, now); err != nil {
		return Appointment{}, err
	}
	// Atomic: take the new slot before releasing the old one.
	nl.Booked++
	if ol, ok := m.slots[a.SlotID]; ok && ol.Booked > 0 {
		ol.Booked--
	}
	a.SlotID, a.Start, a.End = newSlotID, nl.Start, nl.End
	a.Service, a.ResourceName = nl.Service, nl.ResourceName
	return *a, nil
}

var transitions = map[string]struct{ from, to []string }{
	"checkin":  {from: []string{"booked"}, to: []string{"checked-in"}},
	"complete": {from: []string{"checked-in", "booked"}, to: []string{"completed"}},
	"noshow":   {from: []string{"booked"}, to: []string{"no-show"}},
}

func (m *MemoryStore) Transition(appointmentID, action string) (Appointment, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.appts[appointmentID]
	if !ok {
		return Appointment{}, ErrNotFound
	}
	tr, ok := transitions[action]
	if !ok {
		return Appointment{}, ErrBadTransition
	}
	for _, from := range tr.from {
		if a.Status == from {
			a.Status = tr.to[0]
			return *a, nil
		}
	}
	return Appointment{}, ErrBadTransition
}

func (m *MemoryStore) Appointment(id string) (Appointment, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.appts[id]
	if !ok {
		return Appointment{}, ErrNotFound
	}
	return *a, nil
}

func (m *MemoryStore) PatientAppointments(patientID string) []Appointment {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Appointment{}
	for _, a := range m.appts {
		if a.PatientID == patientID {
			out = append(out, *a)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Start < out[j].Start })
	return out
}

func (m *MemoryStore) DayList(facilityID, date string) []Appointment {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []Appointment{}
	for _, a := range m.appts {
		if a.FacilityID == facilityID && len(a.Start) >= 10 && a.Start[:10] == date &&
			a.Status != "cancelled" {
			out = append(out, *a)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Start < out[j].Start })
	return out
}

func (m *MemoryStore) Waitlist(patientID, facilityID, service string, now time.Time) (WaitlistEntry, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if patientID == "" || facilityID == "" || service == "" {
		return WaitlistEntry{}, ErrBadSchedule
	}
	for _, w := range m.waitlist {
		if w.PatientID == patientID && w.FacilityID == facilityID && w.Service == service {
			return *w, nil // already queued — idempotent
		}
	}
	w := &WaitlistEntry{
		ID: m.nextID("WAIT"), PatientID: patientID, FacilityID: facilityID,
		Service: service, CreatedAt: now.UTC().Format(time.RFC3339),
	}
	m.waitlist[w.ID] = w
	return *w, nil
}

func (m *MemoryStore) WaitlistFor(facilityID, service string) []WaitlistEntry {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := []WaitlistEntry{}
	for _, w := range m.waitlist {
		if w.FacilityID == facilityID && (service == "" || w.Service == service) {
			out = append(out, *w)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].CreatedAt != out[j].CreatedAt {
			return out[i].CreatedAt < out[j].CreatedAt
		}
		return out[i].ID < out[j].ID
	})
	return out
}

var _ Store = (*MemoryStore)(nil)
