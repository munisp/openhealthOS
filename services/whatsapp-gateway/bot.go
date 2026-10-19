// bot.go — the dialogue engine. A numbered menu over chat, because
// interactive list payloads are a Cloud-API luxury and plain text is
// the floor every gateway and every phone already supports.
//
// States form a small tree: menu → one branch per service → terminal
// confirmation. "menu" or "0" from anywhere resets to the root — in a
// chat channel, the escape hatch must be one keystroke. All user-facing
// strings come from i18n.go; the session carries the patient's language.
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
	StateLangPick    = "lang_pick"
	StateBindID      = "bind_id"    // matrix: awaiting patient ID
	StateBindPhone   = "bind_phone" // matrix: awaiting registered phone
)

// choice is one numbered option shown to the patient.
type choice struct {
	ID    string
	Label string
}

type Session struct {
	Channel   string // "whatsapp" | "matrix"
	Phone     string // WhatsApp MSISDN, or registered phone (matrix, after binding)
	PatientID string // resolved registry patient id
	Name      string
	Lang      string // en | pcm | ha | yo | ig (i18n.go)
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

// Handle advances the session one message and returns the reply text.
// "" means stay silent (non-text events, empty input).
func (s *service) Handle(ss *Session, text string) string {
	low := strings.ToLower(strings.TrimSpace(text))

	// Language switch works from anywhere, in any language's word for it.
	if code, ok := langKeywords[low]; ok {
		if code == "picker" {
			return s.languagePicker(ss)
		}
		ss.Lang = code
		return ss.tr("lang_set", langName(code)) + "\n" + ss.tr("menu")
	}

	// The Matrix channel binds identity explicitly (no MSISDN to trust):
	// patient ID + registered phone, validated by the portal gate.
	if ss.Channel == "matrix" && ss.PatientID == "" {
		if ss.State == StateLangPick {
			return s.pickLanguage(ss, low)
		}
		return s.bindMatrix(ss, low, text)
	}

	// Escape hatches first, from any state.
	if low == "menu" || low == "hi" || low == "hello" || low == "start" || low == "sannu" || low == "ndewo" || low == "pẹlẹ" || low == "pele" {
		ss.State = StateMenu
		return s.greet(ss)
	}
	if low == "0" && ss.State != StateMenu {
		ss.State = StateMenu
		return ss.tr("menu")
	}

	// Identity gate: everything past the menu needs a bound patient.
	if ss.PatientID == "" {
		id, name, err := s.resolvePatient(ss.Phone)
		if err != nil {
			return ss.tr("registry_down")
		}
		if id == "" {
			return ss.tr("not_registered")
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
	case StateLangPick:
		return s.pickLanguage(ss, low)
	}
	ss.State = StateMenu
	return ss.tr("menu")
}

func (s *service) greet(ss *Session) string {
	g := ss.tr("hello")
	if ss.Name != "" {
		g = ss.tr("hello_name", strings.Split(ss.Name, " ")[0])
	}
	return g + ss.tr("intro") + "\n" + ss.tr("menu")
}

// ---- 7. language ----

func (s *service) languagePicker(ss *Session) string {
	ss.State = StateLangPick
	ss.Choices = nil
	var b strings.Builder
	b.WriteString(ss.tr("lang_prompt"))
	for i, l := range LangNames {
		ss.Choices = append(ss.Choices, choice{ID: l.Code, Label: l.Name})
		fmt.Fprintf(&b, "%d. %s\n", i+1, l.Name)
	}
	return strings.TrimRight(b.String(), "\n")
}

func (s *service) pickLanguage(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return ss.tr("lang_prompt")
	}
	ss.Lang = c.ID
	ss.State = StateMenu
	return ss.tr("lang_set", c.Label) + "\n" + ss.tr("menu")
}

func langName(code string) string {
	for _, l := range LangNames {
		if l.Code == code {
			return l.Name
		}
	}
	return code
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
		return ss.tr("ask_prompt")
	case "6":
		ss.State = StateLogType
		return ss.tr("log_type_prompt")
	case "7":
		return s.languagePicker(ss)
	default:
		return ss.tr("menu")
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
		return ss.tr("facilities_down")
	}
	if len(out.Facilities) == 0 {
		return ss.tr("no_facilities")
	}
	ss.Choices = nil
	var b strings.Builder
	b.WriteString(ss.tr("facilities_prompt"))
	for i, f := range out.Facilities {
		if i == 8 {
			break
		}
		ss.Choices = append(ss.Choices, choice{ID: f.ID, Label: f.Name})
		fmt.Fprintf(&b, "%d. %s\n", i+1, f.Name)
	}
	b.WriteString(ss.tr("or_cancel"))
	ss.State = StateBookFac
	return b.String()
}

func (s *service) pickFacility(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return ss.tr("pick_facility_err")
	}
	ss.Data["facility_id"] = c.ID
	ss.Data["facility"] = c.Label
	ss.Choices = nil
	var b strings.Builder
	b.WriteString(ss.tr("which_day"))
	today := time.Now().UTC()
	for i := 1; i <= 5; i++ {
		d := today.AddDate(0, 0, i).Format("2006-01-02")
		label := today.AddDate(0, 0, i).Format("Mon 2 Jan")
		ss.Choices = append(ss.Choices, choice{ID: d, Label: label})
		fmt.Fprintf(&b, "%d. %s\n", i, label)
	}
	b.WriteString(ss.tr("or_cancel"))
	ss.State = StateBookDate
	return b.String()
}

func (s *service) pickDate(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return ss.tr("pick_day_err")
	}
	ss.Data["date"] = c.ID
	ss.Data["date_label"] = c.Label
	var out struct {
		Slots []struct {
			ID    string `json:"id"`
			Start string `json:"start"`
			Cap   int    `json:"capacity"`
			Book  int    `json:"booked"`
		} `json:"slots"`
	}
	q := ss.portalQ()
	q.Set("facility_id", ss.Data["facility_id"])
	q.Set("date", c.ID)
	if err := s.getJSON(s.portal, "/portal/slots", q, &out); err != nil {
		return ss.tr("slots_down")
	}
	ss.Choices = nil
	var b strings.Builder
	b.WriteString(ss.tr("free_times", ss.Data["facility"], ss.Data["date_label"]))
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
		return ss.tr("no_slots", ss.Data["facility"])
	}
	b.WriteString(ss.tr("or_cancel"))
	ss.State = StateBookSlot
	return b.String()
}

func (s *service) pickSlot(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return ss.tr("pick_slot_err")
	}
	ss.Data["slot_id"] = c.ID
	ss.Data["slot_label"] = c.Label
	ss.State = StateBookConfirm
	return ss.tr("confirm_booking", ss.Data["facility"], ss.Data["date_label"], ss.Data["slot_label"])
}

func (s *service) confirmBooking(ss *Session, low string) string {
	if low != "1" {
		ss.State = StateMenu
		return ss.tr("booking_cancelled") + "\n" + ss.tr("menu")
	}
	var out map[string]any
	err := s.postJSON(s.portal, "/portal/appointments", map[string]string{
		"patient_id": ss.PatientID, "phone": ss.Phone, "slot_id": ss.Data["slot_id"],
	}, &out)
	ss.State = StateMenu
	if err != nil {
		return ss.tr("booking_failed") + "\n" + ss.tr("menu")
	}
	return ss.tr("booked", ss.Data["facility"], ss.Data["date_label"], ss.Data["slot_label"]) +
		"\n\n" + ss.tr("menu")
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
		return ss.tr("appts_down")
	}
	ss.Choices = nil
	var b strings.Builder
	b.WriteString(ss.tr("appts_header"))
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
		return ss.tr("no_appts") + "\n" + ss.tr("menu")
	}
	b.WriteString(ss.tr("appt_cancel_hint"))
	ss.State = StateApptCancel
	return b.String()
}

func (s *service) cancelAppointment(ss *Session, low string) string {
	c, ok := pickN(ss.Choices, low)
	if !ok {
		return ss.tr("appt_cancel_hint")
	}
	err := s.postJSON(s.portal, "/portal/appointments/cancel", map[string]string{
		"patient_id": ss.PatientID, "phone": ss.Phone, "appointment_id": c.ID,
	}, nil)
	ss.State = StateMenu
	if err != nil {
		return ss.tr("cancel_err") + "\n" + ss.tr("menu")
	}
	return ss.tr("cancelled_appt", c.Label) + "\n" + ss.tr("menu")
}

// ---- 3. coverage ----

func (s *service) coverage(ss *Session) string {
	var out map[string]any
	if err := s.getJSON(s.portal, "/portal/coverage", ss.portalQ(), &out); err != nil {
		return ss.tr("coverage_down")
	}
	status, _ := out["status"].(string)
	scheme, _ := out["scheme"].(string)
	if status == "" {
		status = "not-enrolled"
	}
	var msg string
	switch status {
	case "eligible":
		msg = ss.tr("covered")
		if scheme != "" {
			msg += ss.tr("covered_scheme", scheme)
		}
		msg += ss.tr("covered_tail")
	case "suspended":
		msg = ss.tr("suspended")
	default:
		msg = ss.tr("not_enrolled")
	}
	return msg + "\n" + ss.tr("menu")
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
		return ss.tr("updates_down")
	}
	if len(out.Notifications) == 0 {
		return ss.tr("no_updates") + "\n" + ss.tr("menu")
	}
	var b strings.Builder
	b.WriteString(ss.tr("updates_header"))
	for i, ntf := range out.Notifications {
		if i == 5 {
			break
		}
		fmt.Fprintf(&b, "• %s\n", ntf.Body)
	}
	b.WriteString("\n" + ss.tr("menu"))
	return b.String()
}

// ---- 5. ask the clinic ----

func (s *service) askClinic(ss *Session, text string) string {
	err := s.postJSON(s.portal, "/portal/messages", map[string]string{
		"patient_id": ss.PatientID, "phone": ss.Phone, "complaint": text,
	}, nil)
	ss.State = StateMenu
	if err != nil {
		return ss.tr("ask_failed") + "\n" + ss.tr("menu")
	}
	return ss.tr("ask_sent") + "\n" + ss.tr("menu")
}

// ---- 6. log a health reading (wellness rail, source=manual) ----

func (s *service) logType(ss *Session, low string) string {
	switch low {
	case "1":
		ss.Data["log_type"] = "steps"
		ss.State = StateLogValue
		return ss.tr("log_steps_prompt")
	case "2":
		ss.Data["log_type"] = "weight"
		ss.State = StateLogValue
		return ss.tr("log_weight_prompt")
	case "3":
		ss.Data["log_type"] = "bp"
		ss.State = StateLogValue
		return ss.tr("log_bp_prompt")
	default:
		return ss.tr("log_type_err")
	}
}

func (s *service) logValue(ss *Session, low string) string {
	lt := ss.Data["log_type"]
	obs := map[string]any{"ts": time.Now().UTC().Format(time.RFC3339),
		"ref": fmt.Sprintf("wa-%s-%d", ss.Phone, time.Now().Unix())}
	if lt == "bp" {
		parts := strings.Split(low, "/")
		if len(parts) != 2 {
			return ss.tr("bp_format_err")
		}
		sys, err1 := strconv.ParseFloat(strings.TrimSpace(parts[0]), 64)
		dia, err2 := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
		if err1 != nil || err2 != nil || sys < 50 || sys > 300 || dia < 30 || dia > 200 {
			return ss.tr("bp_range_err")
		}
		obs["type"] = "bp"
		obs["value"] = sys
		obs["meta"] = map[string]any{"diastolic": dia, "unit": "mmHg"}
	} else {
		v, err := strconv.ParseFloat(low, 64)
		if err != nil || v < 0 || v > 100000 {
			return ss.tr("number_err")
		}
		obs["type"] = lt
		obs["value"] = v
	}
	err := s.postJSON(s.wellness, "/wellness/ingest", map[string]any{
		"patient_id": ss.PatientID, "source": "manual", "observations": []any{obs},
	}, nil)
	ss.State = StateMenu
	if err != nil {
		return ss.tr("log_failed") + "\n" + ss.tr("menu")
	}
	return ss.tr("log_saved") + "\n" + ss.tr("menu")
}

// pickN resolves a 1-based numbered reply against the current choices.
func pickN(choices []choice, low string) (choice, bool) {
	n, err := strconv.Atoi(low)
	if err != nil || n < 1 || n > len(choices) {
		return choice{}, false
	}
	return choices[n-1], true
}

// ---- matrix identity binding ----

func (s *service) bindMatrix(ss *Session, low, text string) string {
	switch ss.State {
	case StateBindID:
		if ss.Data["awaiting"] == "" {
			ss.Data["awaiting"] = "id"
			return ss.tr("mx_welcome")
		}
		id := strings.TrimSpace(text)
		if id == "" || strings.ContainsAny(id, " \t\n") && len(strings.Fields(id)) > 2 {
			return ss.tr("mx_ask_id")
		}
		// Allow "OH-KD-0001 08012345678" in one message.
		if parts := strings.Fields(id); len(parts) == 2 {
			ss.Data["patient_id"] = parts[0]
			ss.State = StateBindPhone
			return s.bindMatrix(ss, normPhone(parts[1]), parts[1])
		}
		ss.Data["patient_id"] = id
		ss.State = StateBindPhone
		return ss.tr("mx_ask_phone")
	case StateBindPhone:
		phone := normPhone(text)
		if phone == "" {
			return ss.tr("mx_ask_phone")
		}
		pid := ss.Data["patient_id"]
		// The portal gate is the arbiter: 200 binds, 404 reveals nothing.
		var probe map[string]any
		err := s.getJSON(s.portal, "/portal/coverage", url.Values{
			"patient_id": {pid}, "phone": {phone},
		}, &probe)
		if err != nil {
			ss.State = StateBindID
			ss.Data = map[string]string{}
			return ss.tr("mx_bind_fail")
		}
		ss.PatientID, ss.Phone = pid, phone
		ss.State = StateMenu
		ss.Data = map[string]string{}
		return s.greet(ss)
	}
	ss.State = StateBindID
	return ss.tr("mx_welcome")
}
