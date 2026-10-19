// bot.go — the dialogue engine. A numbered menu over chat, because
// interactive list payloads are a Cloud-API luxury and plain text is
// the floor every gateway and every phone already supports.
//
// States form a small tree: menu → one branch per service → terminal
// confirmation. "menu" or "0" from anywhere resets to the root — in a
// chat channel, the escape hatch must be one keystroke.
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

// States.
const (
	StateMenu        = "menu"
	StateBookFac     = "book_facility"
	StateBookDate    = "book_date"
	StateBookSlot    = "book_slot"
	StateBookConfirm = "book_confirm"
	StateApptCancel  = "appt_cancel"
	StateAsk         = "ask"
	StateLogType     = "log_type"
	StateLogValue    = "log_value"
)

// choice is one numbered option shown to the patient.
type choice struct {
	ID    string
	Label string
}

type Session struct {
	Phone     string // WhatsApp MSISDN (digits)
	PatientID string // resolved registry patient id
	Name      string
	State     string
	Choices   []choice // options of the current prompt
	Data      map[string]string
	UpdatedAt time.Time
}

// ---------- tiny platform client (bot is a thin portal client) ----------

func (s *service) getJSON(base, path string, q url.Values, out any) error {
	u := base + path
	if len(q) > 0 {
		u += "?" + q.Encode()
	}
	resp, err := http.Get(u)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		return fmt.Errorf("%s %s: %s", base, path, resp.Status)
	}
	return json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(out)
}

func (s *service) postJSON(base, path string, body, out any) error {
	raw, _ := json.Marshal(body)
	resp, err := http.Post(base+path, "application/json", strings.NewReader(string(raw)))
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 300 {
		io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<16))
		return fmt.Errorf("%s %s: %s", base, path, resp.Status)
	}
	if out != nil {
		return json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(out)
	}
	return nil
}

// normPhone reduces a phone to comparable digits: +234801…, 0801… and
// 234801… all become 234801….
func normPhone(p string) string {
	var b strings.Builder
	for _, r := range p {
		if r >= '0' && r <= '9' {
			b.WriteRune(r)
		}
	}
	d := b.String()
	if strings.HasPrefix(d, "0") && len(d) == 11 { // Nigerian local form
		d = "234" + d[1:]
	}
	return d
}

// resolvePatient binds the WhatsApp number to exactly one registry
// patient. Zero matches → enrolment guidance; the search is prefix
// full-text, so we insist on an exact normalized phone equality —
// anything looser would leak one patient's channel to another's.
func (s *service) resolvePatient(phoneDigits string) (id, name string, err error) {
	var res struct {
		Results []struct {
			ID    string `json:"id"`
			Name  string `json:"name"`
			Phone string `json:"phone"`
		} `json:"results"`
	}
	if err = s.getJSON(s.registry, "/patients/search", url.Values{"q": {phoneDigits}}, &res); err != nil {
		return "", "", err
	}
	for _, p := range res.Results {
		if normPhone(p.Phone) == normPhone(phoneDigits) {
			return p.ID, p.Name, nil
		}
	}
	return "", "", nil
}

// portalQ is the gate pair every portal call carries.
func (s *Session) portalQ() url.Values {
	return url.Values{"patient_id": {s.PatientID}, "phone": {s.Phone}}
}

// ---------- the dialogue ----------

const menuText = `Reply with a number:
1. Book an appointment
2. My appointments
3. My coverage
4. Clinic updates
5. Ask the clinic
6. Log a health reading
0. This menu`

// Handle advances the session one message and returns the reply text.
// "" means stay silent (non-text events, empty input).
func (s *service) Handle(ss *Session, text string) string {
	low := strings.ToLower(strings.TrimSpace(text))

	// Escape hatches first, from any state.
	if low == "menu" || low == "hi" || low == "hello" || low == "start" {
		ss.State = StateMenu
		return s.greet(ss)
	}
	if low == "0" && ss.State != StateMenu {
		ss.State = StateMenu
		return menuText
	}

	// Identity gate: everything past the menu needs a bound patient.
	if ss.PatientID == "" {
		id, name, err := s.resolvePatient(ss.Phone)
		if err != nil {
			return "Our registry is unreachable right now — please try again shortly."
		}
		if id == "" {
			return "This number isn't linked to a health record yet. Visit your nearest primary health centre with this phone, or dial the enrolment USSD code, and come back — your record will find you here."
		}
		ss.PatientID, ss.Name = id, name
	}

	switch ss.State {
	case StateMenu:
		return s.menu(ss, low)
	case StateBookFac:
		return s.pickFacility(ss, low)
	case StateBookDate:
		return s.pickDate(ss, low)
	case StateBookSlot:
		return s.pickSlot(ss, low)
	case StateBookConfirm:
		return s.confirmBooking(ss, low)
	case StateApptCancel:
		return s.cancelAppointment(ss, low)
	case StateAsk:
		return s.askClinic(ss, text)
	case StateLogType:
		return s.logType(ss, low)
	case StateLogValue:
		return s.logValue(ss, low)
	}
	ss.State = StateMenu
	return menuText
}

func (s *service) greet(ss *Session) string {
	if ss.PatientID == "" {
		if _, _, err := s.resolvePatient(ss.Phone); err == nil {
			// resolve lazily in the gate above; greeting stays generic
		}
	}
	g := "Hello!"
	if ss.Name != "" {
		g = "Hello " + strings.Split(ss.Name, " ")[0] + "!"
	}
	return g + " This is your health channel.\n" + menuText
}

func (s *service) menu(ss *Session, low string) string {
	switch low {
	case "1":
		return s.startBooking(ss)
	case "2":
		return s.listAppointments(ss)
	case "3":
		return s.coverage(ss)
	case "4":
		return s.clinicUpdates(ss)
	case "5":
		ss.State = StateAsk
		return "Type your question for the clinic in one message. A nurse will see it in the triage queue.\n(0 to cancel)"
	case "6":
		ss.State = StateLogType
		return "What are you logging?\n1. Steps today\n2. Weight (kg)\n3. Blood pressure (e.g. 120/80)\n(0 to cancel)"
	default:
		return menuText
	}
}

// ---- 1. booking ----

func (s *service) startBooking(ss *Session) string {
	var out struct {
		Facilities []struct {
			ID   string `json:"id"`
			Name string `json:"name"`
		} `json:"facilities"`
	}
	if err := s.getJSON(s.portal, "/portal/facilities", ss.portalQ(), &out); err != nil {
		return "I can't reach the facility list right now — please try again shortly."
	}
	if len(out.Facilities) == 0 {
		return "No facilities are open for self-booking yet. Your clinic can book for you."
	}
	ss.Choices = nil
	var b strings.Builder
	b.WriteString("Where would you like to book? Reply with a number:\n")
	for i, f := range out.Facilities {
		if i == 8 {
			break
		}
		ss.Choices = append(ss.Choices, choice{ID: f.ID, Label: f.Name})
		fmt.Fprintf(&b, "%d. %s\n", i+1, f.Name)
	}
	b.WriteString("(0 to cancel)")
	ss.State = StateBookFac
	return b.String()
}

func (s *service) pickFacility(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return "Reply with the number of the facility, or 0 to cancel."
	}
	ss.Data["facility_id"] = c.ID
	ss.Data["facility"] = c.Label
	// Next five days as date choices.
	ss.Choices = nil
	var b strings.Builder
	b.WriteString("Which day? Reply with a number:\n")
	today := time.Now().UTC()
	for i := 1; i <= 5; i++ {
		d := today.AddDate(0, 0, i).Format("2006-01-02")
		label := today.AddDate(0, 0, i).Format("Mon 2 Jan")
		ss.Choices = append(ss.Choices, choice{ID: d, Label: label})
		fmt.Fprintf(&b, "%d. %s\n", i, label)
	}
	b.WriteString("(0 to cancel)")
	ss.State = StateBookDate
	return b.String()
}

func (s *service) pickDate(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return "Reply with the number of the day, or 0 to cancel."
	}
	ss.Data["date"] = c.ID
	ss.Data["date_label"] = c.Label
	var out struct {
		Slots []struct {
			ID    string `json:"id"`
			Start string `json:"start"`
			Free  int    `json:"-"`
			Cap   int    `json:"capacity"`
			Book  int    `json:"booked"`
		} `json:"slots"`
	}
	q := ss.portalQ()
	q.Set("facility_id", ss.Data["facility_id"])
	q.Set("date", c.ID)
	if err := s.getJSON(s.portal, "/portal/slots", q, &out); err != nil {
		return "I can't reach the appointment book right now — please try again shortly."
	}
	ss.Choices = nil
	var b strings.Builder
	fmt.Fprintf(&b, "Free times at %s on %s:\n", ss.Data["facility"], ss.Data["date_label"])
	n := 0
	for _, sl := range out.Slots {
		if sl.Cap-sl.Book <= 0 {
			continue
		}
		t := sl.Start
		if ts, err := time.Parse(time.RFC3339, sl.Start); err == nil {
			t = ts.Format("15:04")
		}
		n++
		ss.Choices = append(ss.Choices, choice{ID: sl.ID, Label: t})
		fmt.Fprintf(&b, "%d. %s\n", n, t)
		if n == 6 {
			break
		}
	}
	if n == 0 {
		ss.State = StateBookFac
		return "No free slots that day at " + ss.Data["facility"] + ". Pick another day or facility.\n(0 to cancel)"
	}
	b.WriteString("(0 to cancel)")
	ss.State = StateBookSlot
	return b.String()
}

func (s *service) pickSlot(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return "Reply with the number of the time, or 0 to cancel."
	}
	ss.Data["slot_id"] = c.ID
	ss.Data["slot_label"] = c.Label
	ss.State = StateBookConfirm
	return fmt.Sprintf("Book %s at %s on %s?\n1. Yes, book it\n2. No, start over",
		ss.Data["facility"], ss.Data["date_label"], ss.Data["slot_label"])
}

func (s *service) confirmBooking(ss *Session, low string) string {
	if low != "1" {
		ss.State = StateMenu
		return "Cancelled.\n" + menuText
	}
	var out map[string]any
	err := s.postJSON(s.portal, "/portal/appointments", map[string]string{
		"patient_id": ss.PatientID, "phone": ss.Phone, "slot_id": ss.Data["slot_id"],
	}, &out)
	ss.State = StateMenu
	if err != nil {
		return "That slot couldn't be booked — it may have just filled. Pick another time.\n" + menuText
	}
	return fmt.Sprintf("Booked: %s, %s at %s. Bring your patient card; we'll remind you the day before.\n\n%s",
		ss.Data["facility"], ss.Data["date_label"], ss.Data["slot_label"], menuText)
}

// ---- 2. my appointments ----

func (s *service) listAppointments(ss *Session) string {
	var out struct {
		Appointments []struct {
			ID       string `json:"id"`
			Facility string `json:"facility_id"`
			Service  string `json:"service"`
			Start    string `json:"start"`
			Status   string `json:"status"`
		} `json:"appointments"`
	}
	if err := s.getJSON(s.portal, "/portal/appointments", ss.portalQ(), &out); err != nil {
		return "I can't reach your appointments right now — please try again shortly."
	}
	ss.Choices = nil
	var b strings.Builder
	b.WriteString("Your appointments:\n")
	n := 0
	for _, a := range out.Appointments {
		if a.Status != "booked" && a.Status != "checked-in" {
			continue
		}
		when := a.Start
		if ts, err := time.Parse(time.RFC3339, a.Start); err == nil {
			when = ts.Format("Mon 2 Jan 15:04")
		}
		n++
		ss.Choices = append(ss.Choices, choice{ID: a.ID, Label: when})
		fmt.Fprintf(&b, "%d. %s — %s (%s)\n", n, when, a.Service, a.Facility)
	}
	if n == 0 {
		ss.State = StateMenu
		return "You have no upcoming appointments.\n" + menuText
	}
	b.WriteString("\nReply with a number to cancel that appointment, or 0 for the menu.")
	ss.State = StateApptCancel
	return b.String()
}

func (s *service) cancelAppointment(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return "Reply with the number of the appointment to cancel, or 0 for the menu."
	}
	err := s.postJSON(s.portal, "/portal/appointments/cancel", map[string]string{
		"patient_id": ss.PatientID, "phone": ss.Phone, "appointment_id": c.ID,
	}, nil)
	ss.State = StateMenu
	if err != nil {
		return "That appointment couldn't be cancelled — please call the clinic.\n" + menuText
	}
	return "Cancelled: " + c.Label + ". The slot goes back to the book for someone else.\n" + menuText
}

// ---- 3. coverage ----

func (s *service) coverage(ss *Session) string {
	var out map[string]any
	if err := s.getJSON(s.portal, "/portal/coverage", ss.portalQ(), &out); err != nil {
		return "I can't reach the coverage desk right now — please try again shortly."
	}
	status, _ := out["status"].(string)
	scheme, _ := out["scheme"].(string)
	if status == "" {
		status = "not-enrolled"
	}
	var msg string
	switch status {
	case "eligible":
		msg = "You are covered"
		if scheme != "" {
			msg += " under " + scheme
		}
		msg += ". Show your patient card at any empanelled facility."
	case "suspended":
		msg = "Your cover is currently suspended — the scheme office can tell you why."
	default:
		msg = "We found no active enrolment for you. Visit your nearest PHC to enrol — the basic package is tax-funded."
	}
	return msg + "\n" + menuText
}

// ---- 4. clinic updates ----

func (s *service) clinicUpdates(ss *Session) string {
	var out struct {
		Notifications []struct {
			Kind string `json:"kind"`
			Body string `json:"body"`
			Ts   string `json:"ts"`
		} `json:"notifications"`
	}
	if err := s.getJSON(s.portal, "/portal/feed", ss.portalQ(), &out); err != nil {
		return "I can't reach your clinic updates right now — please try again shortly."
	}
	if len(out.Notifications) == 0 {
		return "No updates from your clinic yet.\n" + menuText
	}
	var b strings.Builder
	b.WriteString("Your latest clinic updates:\n")
	for i, ntf := range out.Notifications {
		if i == 5 {
			break
		}
		fmt.Fprintf(&b, "• %s\n", ntf.Body)
	}
	b.WriteString("\n" + menuText)
	return b.String()
}

// ---- 5. ask the clinic ----

func (s *service) askClinic(ss *Session, text string) string {
	err := s.postJSON(s.portal, "/portal/messages", map[string]string{
		"patient_id": ss.PatientID, "phone": ss.Phone, "complaint": text,
	}, nil)
	ss.State = StateMenu
	if err != nil {
		return "Your message couldn't be sent right now — please try again shortly.\n" + menuText
	}
	return "Sent. A nurse will see it in the triage queue and reply here.\n" + menuText
}

// ---- 6. log a health reading (wellness rail, source=manual) ----

func (s *service) logType(ss *Session, low string) string {
	switch low {
	case "1":
		ss.Data["log_type"] = "steps"
		ss.State = StateLogValue
		return "How many steps today? (number only, 0 to cancel)"
	case "2":
		ss.Data["log_type"] = "weight"
		ss.State = StateLogValue
		return "Your weight in kg? (e.g. 72.5, 0 to cancel)"
	case "3":
		ss.Data["log_type"] = "bp"
		ss.State = StateLogValue
		return "Your blood pressure? (e.g. 120/80, 0 to cancel)"
	default:
		return "Reply 1 for steps, 2 for weight, 3 for blood pressure — or 0 to cancel."
	}
}

func (s *service) logValue(ss *Session, low string) string {
	lt := ss.Data["log_type"]
	obs := map[string]any{"ts": time.Now().UTC().Format(time.RFC3339),
		"ref": fmt.Sprintf("wa-%s-%d", ss.Phone, time.Now().Unix())}
	if lt == "bp" {
		parts := strings.Split(low, "/")
		if len(parts) != 2 {
			return "Use the format 120/80, or 0 to cancel."
		}
		sys, err1 := strconv.ParseFloat(strings.TrimSpace(parts[0]), 64)
		dia, err2 := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
		if err1 != nil || err2 != nil || sys < 50 || sys > 300 || dia < 30 || dia > 200 {
			return "That doesn't look like a blood pressure. Use e.g. 120/80, or 0 to cancel."
		}
		obs["type"] = "bp"
		obs["value"] = sys
		obs["meta"] = map[string]any{"diastolic": dia, "unit": "mmHg"}
	} else {
		v, err := strconv.ParseFloat(low, 64)
		if err != nil || v < 0 || v > 100000 {
			return "A plain number please, or 0 to cancel."
		}
		obs["type"] = lt
		obs["value"] = v
	}
	err := s.postJSON(s.wellness, "/wellness/ingest", map[string]any{
		"patient_id": ss.PatientID, "source": "manual", "observations": []any{obs},
	}, nil)
	ss.State = StateMenu
	if err != nil {
		return "That reading couldn't be saved right now — please try again shortly.\n" + menuText
	}
	return "Logged to your record — your care team can see it, and it shapes your prevention plan.\n" + menuText
}

// pickN resolves a 1-based numbered reply against the current choices.
func pickN(choices []choice, low string) (choice, bool) {
	n, err := strconv.Atoi(low)
	if err != nil || n < 1 || n > len(choices) {
		return choice{}, false
	}
	return choices[n-1], true
}
