// adherence.go — the medication adherence guardian (innovation 2).
//
// A prescription that is never taken is a treatment that never happened.
// Every MedicationRequest through the FHIR inbox opens an adherence plan:
// a daily dose nudge over the patient's own channel (Matrix first, SMS
// fallback — the same chain as everything else), a refill reminder before
// the course runs out, and a lapse escalation to the facility's CHW queue
// when the refill window lapses uncollected.
//
// The engine is in-process, clock-injected, and honest about its channels:
// nudges are clinical-detail-light ("time for your medication" + the drug
// name the patient already saw on the prescription card), escalations go
// to staff, never patients.
package main

import (
	"fmt"
	"log"
	"sync"
	"time"
)

// Adherence defaults (env-tunable in a real deployment; constants keep the
// engine deterministic for tests).
const (
	adherenceCourseDays  = 30              // standard course length
	adherenceRefillLead  = 3 * 24 * time.Hour // remind 3 days before run-out
	adherenceLapseAfter  = 3 * 24 * time.Hour // uncollected 3 days past due = lapse
	adherenceNudgeHour   = 9               // 09:00 local daily dose nudge
)

// adherencePlan tracks one prescribed course for one patient.
type adherencePlan struct {
	PatientID  string
	// Instruction is the patient-language dosage line (dosagetext,
	// ADR-0049) carried from the prescription event, e.g. "Take 2 tablet
	// two times every day." Empty falls back to the generic nudge.
	Medication  string
	Instruction string
	Started     time.Time
	RefillDue  time.Time
	LastNudge  time.Time // last dose nudge (zero = never)
	RefillSent bool
	Dispensed  bool
	Lapsed     bool
}

// adherence is the engine. All state is in-memory — plans rebuild from the
// notification journal on restart (the prescription events are durable);
// nudges are idempotent per day so a rebuild double-nudges nobody (LastNudge
// resets to zero, today's nudge may repeat once — acceptable, never silent).
type adherence struct {
	mu    sync.Mutex
	plans []*adherencePlan
	svc   *Service
	now   func() time.Time
}

func newAdherence(svc *Service) *adherence {
	return &adherence{svc: svc, now: time.Now}
}

// onPrescription opens a plan. Idempotent per patient+medication: a
// duplicate MedicationRequest re-arms the same plan instead of stacking.
func (a *adherence) onPrescription(patientID, medication, instruction string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, p := range a.plans {
		if p.PatientID == patientID && p.Medication == medication && !p.Dispensed {
			p.RefillDue = a.now().Add(adherenceCourseDays * 24 * time.Hour)
			p.RefillSent, p.Lapsed = false, false
			if instruction != "" {
				p.Instruction = instruction
			}
			return
		}
	}
	now := a.now()
	a.plans = append(a.plans, &adherencePlan{
		PatientID:   patientID,
		Medication:  medication,
		Instruction: instruction,
		Started:     now,
		RefillDue:   now.Add(adherenceCourseDays * 24 * time.Hour),
	})
}

// onDispensed closes the loop: the pharmacy handed over the medication.
func (a *adherence) onDispensed(patientID, medication string) {
	a.mu.Lock()
	defer a.mu.Unlock()
	for _, p := range a.plans {
		if p.PatientID == patientID && (medication == "" || p.Medication == medication) {
			p.Dispensed = true
		}
	}
}

// plansFor lists active plans (tests + ops endpoint).
func (a *adherence) plansFor(patientID string) []adherencePlan {
	a.mu.Lock()
	defer a.mu.Unlock()
	var out []adherencePlan
	for _, p := range a.plans {
		if p.PatientID == patientID {
			out = append(out, *p)
		}
	}
	return out
}

// tick runs one sweep: dose nudges in the morning window, refill reminders
// inside the lead window, lapse escalations past the grace period. Called
// from the drain loop — rides the same cadence, no new goroutine.
func (a *adherence) tick() {
	now := a.now()
	var nudges []struct{ phone, text string }
	var lapses []adherencePlan

	a.mu.Lock()
	for _, p := range a.plans {
		if p.Dispensed && now.Before(p.RefillDue) {
			continue // collected and course running
		}
		// Daily dose nudge in the 09:00 hour, once per day.
		if now.Hour() == adherenceNudgeHour && !sameDay(p.LastNudge, now) {
			sub, err := a.svc.store.Subscription(p.PatientID)
			if err == nil && sub.Phone != "" {
				p.LastNudge = now
				// Prefer the patient-language dosage line (ADR-0049) over
				// the generic nudge — an instruction beats a reminder.
				text := fmt.Sprintf("OpenHealth: time for your medication (%s). Reply STOP to end reminders.", p.Medication)
				if p.Instruction != "" {
					text = fmt.Sprintf("OpenHealth: %s (%s) Reply STOP to end reminders.", p.Instruction, p.Medication)
				}
				nudges = append(nudges, struct{ phone, text string }{sub.Phone, text})
			}
		}
		// Refill reminder inside the lead window, once.
		if !p.Dispensed && !p.RefillSent && now.After(p.RefillDue.Add(-adherenceRefillLead)) && now.Before(p.RefillDue) {
			sub, err := a.svc.store.Subscription(p.PatientID)
			if err == nil && sub.Phone != "" {
				p.RefillSent = true
				nudges = append(nudges, struct{ phone, text string }{
					sub.Phone, fmt.Sprintf("OpenHealth: your %s course runs out in 3 days — collect your refill at your facility.", p.Medication)})
			}
		}
		// Lapse: refill window passed, never collected → staff escalation.
		if !p.Dispensed && !p.Lapsed && now.After(p.RefillDue.Add(adherenceLapseAfter)) {
			p.Lapsed = true
			lapses = append(lapses, *p)
		}
	}
	a.mu.Unlock()

	for _, n := range nudges {
		if err := a.svc.provider.Send(n.phone, n.text); err != nil {
			log.Printf("adherence: nudge failed: %v", err)
		}
	}
	for _, p := range lapses {
		// Clinical escalation into the patient's howUdeyChat care room
		// (fail-soft) in parallel with the ops SMS rail.
		if sub, err := a.svc.store.Subscription(p.PatientID); err == nil && sub.Phone != "" {
			escalateToCareRoom(sub.Phone, "", "adherence_lapse:"+p.Medication)
		}
		// Staff traffic via the alert rail (ops contacts), never the patient.
		for _, c := range opsContactsFor("") {
			text := fmt.Sprintf("[FIRING] AdherenceLapse (warning): patient %s has not collected %s — 3 days past refill due.", p.PatientID, p.Medication)
			if err := a.svc.provider.Send(c, text); err != nil {
				log.Printf("adherence: lapse alert failed: %v", err)
			}
		}
	}
}

func sameDay(a, b time.Time) bool {
	if a.IsZero() {
		return false
	}
	ay, am, ad := a.Date()
	by, bm, bd := b.Date()
	return ay == by && am == bm && ad == bd
}
