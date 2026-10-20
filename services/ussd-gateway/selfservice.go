// selfservice.go — USSD self-service depth (ADR-0056): appointment booking,
// medication-refill reminders, and results-ready checks over the same session
// tree. Each capability hides behind a nil-reader (state not live yet ->
// honest "not active" message), and every action degrades fail-soft.
package main

import (
	"fmt"
	"strings"
)

// AppointmentsBooker books the next available slot for a clinic code.
type AppointmentsBooker interface {
	BookNext(memberID, clinicCode string) (slot string, err error)
}

// RefillReminder registers a refill reminder for a member.
type RefillReminder interface {
	SetReminder(memberID, drugLabel string, days int) error
	NextRefill(memberID string) (label string, dueInDays int, ok bool)
}

// ResultsChecker reports whether a lab result is ready for collection.
type ResultsChecker interface {
	Status(memberID string) (ready int, pending int, err error)
}

func (g *Gateway) WithAppointments(a AppointmentsBooker) *Gateway { g.appts = a; return g }
func (g *Gateway) WithRefills(r RefillReminder) *Gateway          { g.refills = r; return g }
func (g *Gateway) WithResults(r ResultsChecker) *Gateway          { g.results = r; return g }

const selfServiceMenu = "\n6. Book an appointment\n7. My medicine refill\n8. Are my results ready?"

// menuText composes the top menu, listing self-service lines only when live.
func (g *Gateway) menuText() string {
	m := menu
	if g.appts != nil || g.refills != nil || g.results != nil {
		m = strings.Replace(m, "\n0. Exit", selfServiceMenu+"\n0. Exit", 1)
	}
	return m
}

// handleSelfService routes steps 6-8; returns ("", false) if not applicable.
func (g *Gateway) handleSelfService(s *Session, sessionID, last string) (string, bool) {
	switch s.Step {
	case "menu":
		switch last {
		case "6":
			if g.appts == nil {
				delete(g.sessions, sessionID)
				return "END Appointments are not active in this state yet. Ask at your PHC.", true
			}
			s.Step = "appt-member"
			return "CON Enter your member ID:", true
		case "7":
			if g.refills == nil {
				delete(g.sessions, sessionID)
				return "END Refill reminders are not active in this state yet. Ask at your PHC.", true
			}
			s.Step = "refill-member"
			return "CON Enter your member ID:", true
		case "8":
			if g.results == nil {
				delete(g.sessions, sessionID)
				return "END Result checks are not active in this state yet. Ask at your PHC.", true
			}
			s.Step = "results-member"
			return "CON Enter your member ID:", true
		}
	case "appt-member":
		s.MemberID = last
		s.Step = "appt-clinic"
		return "CON Enter clinic code (1=General 2=ANC 3=Immunization 4=Chronic):", true
	case "appt-clinic":
		delete(g.sessions, sessionID)
		clinics := map[string]string{"1": "GENERAL", "2": "ANC", "3": "IMMUNIZATION", "4": "CHRONIC"}
		code, ok := clinics[last]
		if !ok {
			return "END Invalid clinic code.", true
		}
		slot, err := g.appts.BookNext(s.MemberID, code)
		if err != nil {
			return "END We could not book now. Please try again or visit your PHC.", true
		}
		return fmt.Sprintf("END Booked: %s clinic on %s. Bring your member card.", code, slot), true
	case "refill-member":
		delete(g.sessions, sessionID)
		label, days, ok := g.refills.NextRefill(last)
		if !ok {
			return "END No active refill plan found. Ask the pharmacy to enrol you.", true
		}
		return fmt.Sprintf("END Next refill: %s in %d day(s). We will remind you by SMS.", label, days), true
	case "results-member":
		delete(g.sessions, sessionID)
		ready, pending, err := g.results.Status(last)
		if err != nil {
			return "END We could not check results now. Try again later.", true
		}
		if ready == 0 && pending == 0 {
			return "END No lab requests found for this member ID.", true
		}
		return fmt.Sprintf("END %d result(s) READY for collection, %d still processing.", ready, pending), true
	}
	return "", false
}
