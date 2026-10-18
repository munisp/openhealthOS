package main

// HTTP surface for case management + CRM (ADR-0019): contacts,
// interactions, cases, tasks. Every emergency deserves an owner and a
// follow-through; these endpoints are how the humans see and steer it.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"time"
)

// openCaseForIntake wires the CRM chain after a new incident: contact
// upsert, interaction log, and — for red/orange triage — an automatic
// case with an arrival-check task on the triage SLA clock.
func (s server) openCaseForIntake(inc Incident) {
	if inc.CallerPhone == "" {
		return
	}
	ts := s.ix.now().UTC()
	s.store.UpsertContactOnIntake(inc.CallerPhone, inc.Channel, inc.PatientID, ts.Format(time.RFC3339))
	_ = s.store.AddInteraction(Interaction{
		ID: s.store.NextInteractionID(), Phone: inc.CallerPhone, Channel: inc.Channel,
		Direction: "inbound", Kind: "sos", IncidentID: inc.ID,
		Summary: fmt.Sprintf("%s — %s", inc.Triage, inc.Complaint),
		Actor:   "service", TS: ts.Format(time.RFC3339),
	})
	if inc.Triage == TriageGreen {
		return // green incidents are informational; no case overhead
	}
	if _, err := s.store.OpenCaseFor(inc.CallerPhone); err == nil {
		return // an open case already covers this caller — don't stack them
	}
	caseID := s.store.NextCaseID()
	c := Case{
		ID: caseID, ContactPhone: inc.CallerPhone, PatientID: inc.PatientID,
		IncidentID: inc.ID, Title: fmt.Sprintf("%s emergency — %s", inc.Triage, inc.Complaint),
		Priority: inc.Triage, Status: CaseOpen, Owner: "dispatcher-on-call",
		FacilityID: inc.FacilityID,
		OpenedAt:   ts.Format(time.RFC3339), UpdatedAt: ts.Format(time.RFC3339),
		Timeline: []TimelineEntry{{
			TS: ts.Format(time.RFC3339), Actor: "service", Action: "case-opened",
			Detail: "auto-opened from " + inc.Triage + " triage of " + inc.ID,
		}},
	}
	_ = s.store.PutCase(c)
	due := ts.Add(arrivalSLA[inc.Triage])
	_ = s.store.PutTask(CaseTask{
		ID: s.store.NextTaskID(), CaseID: caseID, Kind: TaskArrivalCheck,
		DueAt: due.Format(time.RFC3339), Status: TaskOpen, Assignee: "dispatcher-on-call",
		Note: "confirm help reached the caller (SLA " + arrivalSLA[inc.Triage].String() + " for " + inc.Triage + ")",
	})
}

// scheduleFollowUp on incident closure: a 48-hour welfare call, but
// only if the contact still consents to follow-up (NDPA).
func (s server) scheduleFollowUp(inc Incident) {
	if inc.CallerPhone == "" {
		return
	}
	c, err := s.store.OpenCaseFor(inc.CallerPhone)
	if err != nil {
		return
	}
	contact, err := s.store.GetContact(inc.CallerPhone)
	if err != nil || !contact.ConsentFollowUp {
		return
	}
	ts := s.ix.now().UTC()
	_ = s.store.PutTask(CaseTask{
		ID: s.store.NextTaskID(), CaseID: c.ID, Kind: TaskFollowUp48h,
		DueAt: ts.Add(48 * time.Hour).Format(time.RFC3339), Status: TaskOpen,
		Assignee: c.Owner,
		Note:     "post-closure welfare check for " + inc.ID,
	})
	if c.Status == CaseOpen || c.Status == CaseInProgress {
		c.Status = CaseFollowUp
		c.UpdatedAt = ts.Format(time.RFC3339)
		c.Timeline = append(c.Timeline, TimelineEntry{
			TS: c.UpdatedAt, Actor: "service", Action: "follow-up-scheduled",
			Detail: "incident " + inc.ID + " closed; 48h welfare call queued",
		})
		_ = s.store.PutCase(c)
	}
}

// ---------------------------------------------------------------- contacts

func (s server) listContacts(w http.ResponseWriter, r *http.Request) {
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	writeJSON(w, 200, map[string]any{
		"contacts": s.store.ListContacts(r.URL.Query().Get("q"), limit),
	})
}

func (s server) getContact(w http.ResponseWriter, r *http.Request) {
	c, err := s.store.GetContact(r.PathValue("phone"))
	if err != nil {
		fail(w, err)
		return
	}
	cases := []Case{}
	for _, k := range s.store.ListCases("", "") {
		if k.ContactPhone == c.Phone {
			cases = append(cases, k)
		}
	}
	writeJSON(w, 200, map[string]any{
		"contact":      c,
		"interactions": s.store.Interactions(c.Phone),
		"cases":        cases,
	})
}

// setConsent is the NDPA lever: a caller can revoke follow-up consent
// at any time, and the task rail must respect it from that moment.
func (s server) setConsent(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ConsentFollowUp bool   `json:"consent_follow_up"`
		Actor           string `json:"actor"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Actor == "" {
		fail(w, fmt.Errorf("%w: consent_follow_up and actor required", ErrValidation))
		return
	}
	c, err := s.store.GetContact(r.PathValue("phone"))
	if err != nil {
		fail(w, err)
		return
	}
	c.ConsentFollowUp = req.ConsentFollowUp
	if err := s.store.PutContact(c); err != nil {
		fail(w, err)
		return
	}
	_ = s.store.AddInteraction(Interaction{
		ID: s.store.NextInteractionID(), Phone: c.Phone, Channel: c.PreferredChannel,
		Direction: "inbound", Kind: "note",
		Summary: fmt.Sprintf("follow-up consent set to %v by %s", req.ConsentFollowUp, req.Actor),
		Actor:   req.Actor, TS: s.ix.now().UTC().Format(time.RFC3339),
	})
	writeJSON(w, 200, c)
}

// addInteraction logs an outbound touch (callback, status update, note)
// against the contact — the CRM remembers who said what, when.
func (s server) addInteraction(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Channel string `json:"channel"`
		Kind    string `json:"kind"`
		Summary string `json:"summary"`
		CaseID  string `json:"case_id"`
		Actor   string `json:"actor"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Summary == "" || req.Actor == "" {
		fail(w, fmt.Errorf("%w: summary and actor required", ErrValidation))
		return
	}
	c, err := s.store.GetContact(r.PathValue("phone"))
	if err != nil {
		fail(w, err)
		return
	}
	kind := req.Kind
	if kind == "" {
		kind = "note"
	}
	i := Interaction{
		ID: s.store.NextInteractionID(), Phone: c.Phone, Channel: req.Channel,
		Direction: "outbound", Kind: kind, Summary: req.Summary, CaseID: req.CaseID,
		Actor: req.Actor, TS: s.ix.now().UTC().Format(time.RFC3339),
	}
	if err := s.store.AddInteraction(i); err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 201, i)
}

// ------------------------------------------------------------------- cases

func (s server) listCases(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, 200, map[string]any{
		"cases": s.store.ListCases(r.URL.Query().Get("status"), r.URL.Query().Get("owner")),
	})
}

func (s server) getCase(w http.ResponseWriter, r *http.Request) {
	c, err := s.store.GetCase(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, map[string]any{
		"case":  c,
		"tasks": s.store.ListTasks(c.ID),
	})
}

func (s server) caseTransition(w http.ResponseWriter, r *http.Request) {
	var req struct {
		To      string `json:"to"`
		Actor   string `json:"actor"`
		Outcome string `json:"outcome"`
		Note    string `json:"note"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.To == "" || req.Actor == "" {
		fail(w, fmt.Errorf("%w: to and actor required", ErrValidation))
		return
	}
	c, err := s.store.GetCase(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	legal := false
	for _, t := range caseTransitions[c.Status] {
		if t == req.To {
			legal = true
			break
		}
	}
	if !legal {
		fail(w, fmt.Errorf("%w: case %s -> %s", ErrBadTransition, c.Status, req.To))
		return
	}
	if req.To == CaseResolved || req.To == CaseClosed {
		if req.Outcome == "" {
			fail(w, fmt.Errorf("%w: outcome required when resolving or closing a case", ErrValidation))
			return
		}
		c.Outcome = req.Outcome
		if req.To == CaseClosed {
			c.ClosedAt = s.ix.now().UTC().Format(time.RFC3339)
		}
	}
	c.Status = req.To
	c.UpdatedAt = s.ix.now().UTC().Format(time.RFC3339)
	c.Timeline = append(c.Timeline, TimelineEntry{
		TS: c.UpdatedAt, Actor: req.Actor, Action: req.To, Detail: req.Note,
	})
	if err := s.store.PutCase(c); err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, c)
}

// ------------------------------------------------------------------- tasks

func (s server) addTask(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Kind     string `json:"kind"`
		DueAt    string `json:"due_at"`
		Assignee string `json:"assignee"`
		Note     string `json:"note"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.DueAt == "" {
		fail(w, fmt.Errorf("%w: due_at required", ErrValidation))
		return
	}
	if _, err := time.Parse(time.RFC3339, req.DueAt); err != nil {
		fail(w, fmt.Errorf("%w: due_at must be RFC3339", ErrValidation))
		return
	}
	c, err := s.store.GetCase(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	kind := req.Kind
	if kind == "" {
		kind = TaskCustom
	}
	t := CaseTask{
		ID: s.store.NextTaskID(), CaseID: c.ID, Kind: kind,
		DueAt: req.DueAt, Status: TaskOpen, Assignee: req.Assignee, Note: req.Note,
	}
	if err := s.store.PutTask(t); err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 201, t)
}

func (s server) taskDone(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Note string `json:"note"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	t, err := s.findTask(r.PathValue("id"))
	if err != nil {
		fail(w, err)
		return
	}
	if t.Status != TaskOpen {
		fail(w, fmt.Errorf("%w: task %s already %s", ErrBadTransition, t.ID, t.Status))
		return
	}
	t.Status = TaskDone
	t.DoneAt = s.ix.now().UTC().Format(time.RFC3339)
	if req.Note != "" {
		t.Note = t.Note + " | done: " + req.Note
	}
	if err := s.store.PutTask(t); err != nil {
		fail(w, err)
		return
	}
	writeJSON(w, 200, t)
}

// findTask locates a task across cases — tasks are few, callers are many.
func (s server) findTask(id string) (CaseTask, error) {
	for _, t := range s.store.OpenTasks() {
		if t.ID == id {
			return t, nil
		}
	}
	// also match done/cancelled so the error says "already done", not 404
	for _, c := range s.store.ListCases("", "") {
		for _, t := range s.store.ListTasks(c.ID) {
			if t.ID == id {
				return t, nil
			}
		}
	}
	return CaseTask{}, ErrNotFound
}

// overdueTasks is the dispatcher's guilt list: open tasks past due,
// oldest first. This endpoint is the SLA made visible.
func (s server) overdueTasks(w http.ResponseWriter, r *http.Request) {
	now := s.ix.now().UTC()
	out := []CaseTask{}
	for _, t := range s.store.OpenTasks() {
		if due, err := time.Parse(time.RFC3339, t.DueAt); err == nil && due.Before(now) {
			out = append(out, t)
		}
	}
	writeJSON(w, 200, map[string]any{"overdue": out, "as_of": now.Format(time.RFC3339)})
}
