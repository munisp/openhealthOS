package main

import (
	"errors"
	"testing"
	"time"
)

// NOW is a Monday (2026-10-19 is a Monday).
var NOW = time.Date(2026, 10, 19, 8, 0, 0, 0, time.UTC)

func storeWithSlots(t *testing.T) (*MemoryStore, Schedule, []Slot) {
	t.Helper()
	m := NewMemoryStore()
	sch, err := m.PutSchedule(Schedule{
		FacilityID: "FAC-1", ResourceID: "NURSE-1", ResourceName: "Nurse Amina",
		ResourceType: "provider", Service: "general",
		Weekday: 1, StartMin: 8 * 60, EndMin: 9 * 60, // Mondays 08:00–09:00
		SlotMinutes: 20, Capacity: 1,
	})
	if err != nil {
		t.Fatalf("schedule: %v", err)
	}
	slots, err := m.Generate(sch.ID, "2026-10-19", NOW) // a Monday
	if err != nil {
		t.Fatalf("generate: %v", err)
	}
	if len(slots) != 3 {
		t.Fatalf("want 3 slots, got %d", len(slots))
	}
	return m, sch, slots
}

func TestGenerateIsIdempotentAndWeekdayBound(t *testing.T) {
	m, sch, slots := storeWithSlots(t)
	// Regeneration keeps booking counts and never duplicates.
	if _, err := m.Book(slots[0].ID, "PT-1", "fever", nil, NOW); err != nil {
		t.Fatalf("book: %v", err)
	}
	again, err := m.Generate(sch.ID, "2026-10-19", NOW)
	if err != nil || len(again) != 3 || again[0].Booked != 1 {
		t.Fatalf("regenerate: %v %+v", err, again)
	}
	// Wrong weekday: no slots, no error.
	none, err := m.Generate(sch.ID, "2026-10-20", NOW) // Tuesday
	if err != nil || len(none) != 0 {
		t.Fatalf("weekday guard: %v %d", err, len(none))
	}
}

func TestNoDoubleBookAndWaitlistPromotion(t *testing.T) {
	m, _, slots := storeWithSlots(t)
	if _, err := m.Book(slots[0].ID, "PT-1", "", nil, NOW); err != nil {
		t.Fatalf("first book: %v", err)
	}
	// Capacity 1 — the second booking must fail.
	if _, err := m.Book(slots[0].ID, "PT-2", "", nil, NOW); !errors.Is(err, ErrSlotFull) {
		t.Fatalf("want ErrSlotFull, got %v", err)
	}
	// PT-2 joins the waitlist; when PT-1 cancels, PT-2 is promoted FIFO.
	if _, err := m.Waitlist("PT-2", "FAC-1", "general", NOW); err != nil {
		t.Fatalf("waitlist: %v", err)
	}
	if _, err := m.Waitlist("PT-3", "FAC-1", "general", NOW.Add(time.Second)); err != nil {
		t.Fatalf("waitlist: %v", err)
	}
	appts := m.PatientAppointments("PT-1")
	cancelled, promoted, err := m.Cancel(appts[0].ID, NOW)
	if err != nil || cancelled.Status != "cancelled" {
		t.Fatalf("cancel: %v %+v", err, cancelled)
	}
	if promoted == nil || promoted.PatientID != "PT-2" {
		t.Fatalf("FIFO promotion must take PT-2 first: %+v", promoted)
	}
	// The slot is full again — PT-3 stays queued.
	if got := m.Slots("FAC-1", "general", "", ""); got[0].Free() != 0 {
		t.Fatalf("slot must be full after promotion: %+v", got[0])
	}
	if wl := m.WaitlistFor("FAC-1", "general"); len(wl) != 1 || wl[0].PatientID != "PT-3" {
		t.Fatalf("waitlist after promotion: %+v", wl)
	}
}

func TestBookingWindow(t *testing.T) {
	m := NewMemoryStore()
	sch, _ := m.PutSchedule(Schedule{
		FacilityID: "FAC-1", ResourceID: "R", ResourceName: "R", ResourceType: "room",
		Service: "theatre", Weekday: 1, StartMin: 480, EndMin: 540, SlotMinutes: 60, Capacity: 1,
	})
	near, _ := m.Generate(sch.ID, "2026-10-26", NOW) // next Monday — inside 30d
	far, _ := m.Generate(sch.ID, "2026-12-21", NOW)  // >30d out
	if _, err := m.Book(near[0].ID, "PT-1", "", nil, NOW); err != nil {
		t.Fatalf("inside window must book: %v", err)
	}
	if _, err := m.Book(far[0].ID, "PT-1", "", nil, NOW); !errors.Is(err, ErrSlotPast) {
		t.Fatalf("beyond window: want ErrSlotPast, got %v", err)
	}
	// A past slot can never be booked.
	past, _ := m.Generate(sch.ID, "2026-10-12", NOW)
	if _, err := m.Book(past[0].ID, "PT-1", "", nil, NOW); !errors.Is(err, ErrSlotPast) {
		t.Fatalf("past slot: want ErrSlotPast, got %v", err)
	}
}

func TestRescheduleIsAtomic(t *testing.T) {
	m, sch, slots := storeWithSlots(t)
	more, _ := m.Generate(sch.ID, "2026-10-26", NOW)
	a, err := m.Book(slots[0].ID, "PT-1", "", nil, NOW)
	if err != nil {
		t.Fatalf("book: %v", err)
	}
	// Fill the target slot — reschedule must fail and leave the old booking intact.
	if _, err := m.Book(more[0].ID, "PT-2", "", nil, NOW); err != nil {
		t.Fatalf("fill target: %v", err)
	}
	if _, err := m.Reschedule(a.ID, more[0].ID, NOW); !errors.Is(err, ErrSlotFull) {
		t.Fatalf("want ErrSlotFull, got %v", err)
	}
	got, _ := m.Appointment(a.ID)
	if got.SlotID != slots[0].ID || got.Status != "booked" {
		t.Fatalf("failed reschedule must leave the booking intact: %+v", got)
	}
	// Free target reschedules cleanly and releases the old slot.
	if _, err := m.Reschedule(a.ID, more[1].ID, NOW); err != nil {
		t.Fatalf("reschedule: %v", err)
	}
	all := m.Slots("FAC-1", "general", "", "")
	if all[0].Booked != 0 {
		t.Fatalf("old slot must be released: %+v", all[0])
	}
}

func TestDayFlowTransitions(t *testing.T) {
	m, _, slots := storeWithSlots(t)
	a, _ := m.Book(slots[0].ID, "PT-1", "", nil, NOW)
	if _, err := m.Transition(a.ID, "complete"); err != nil {
		t.Fatalf("booked → complete (walk-in) allowed: %v", err)
	}
	if _, err := m.Transition(a.ID, "noshow"); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("completed is terminal: want ErrBadTransition, got %v", err)
	}
	b, _ := m.Book(slots[1].ID, "PT-2", "", nil, NOW)
	c, _ := m.Transition(b.ID, "checkin")
	if c.Status != "checked-in" {
		t.Fatalf("checkin: %+v", c)
	}
	// A checked-in patient can no longer cancel — they're in the building.
	if _, _, err := m.Cancel(b.ID, NOW); !errors.Is(err, ErrBadTransition) {
		t.Fatalf("checked-in cancel: want ErrBadTransition, got %v", err)
	}
	// Day board: cancelled appointments disappear, the rest are listed in order.
	d, _ := m.Book(slots[2].ID, "PT-3", "", nil, NOW)
	if _, _, err := m.Cancel(d.ID, NOW); err != nil {
		t.Fatalf("cancel booked: %v", err)
	}
	board := m.DayList("FAC-1", "2026-10-19")
	if len(board) != 2 || board[0].ID != a.ID || board[1].ID != b.ID {
		t.Fatalf("day board: %+v", board)
	}
}

func TestWaitlistIdempotent(t *testing.T) {
	m := NewMemoryStore()
	w1, _ := m.Waitlist("PT-1", "FAC-1", "anc", NOW)
	w2, _ := m.Waitlist("PT-1", "FAC-1", "anc", NOW.Add(time.Hour))
	if w1.ID != w2.ID {
		t.Fatalf("re-joining the same queue must be a no-op: %q vs %q", w1.ID, w2.ID)
	}
}
