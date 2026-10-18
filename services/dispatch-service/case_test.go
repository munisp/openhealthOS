package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func sosIncident(t *testing.T, s server, phone, complaint string) map[string]any {
	t.Helper()
	rec, out := post(t, s, "/sos", `{"phone":"`+phone+`","complaint":"`+complaint+`"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("sos: want 201, got %d", rec.Code)
	}
	return out
}

func TestRedTriageAutoOpensCaseWithArrivalTask(t *testing.T) {
	s := newTestServer()
	s.ix.now = func() time.Time { return time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC) }
	sosIncident(t, s, "+234901", "unconscious and not responding")

	cases := s.store.ListCases("", "")
	if len(cases) != 1 {
		t.Fatalf("red triage must auto-open exactly one case, got %d", len(cases))
	}
	c := cases[0]
	if c.Priority != TriageRed || c.Status != CaseOpen || c.Owner == "" {
		t.Fatalf("case shape wrong: %+v", c)
	}
	tasks := s.store.ListTasks(c.ID)
	if len(tasks) != 1 || tasks[0].Kind != TaskArrivalCheck {
		t.Fatalf("arrival-check task expected, got %+v", tasks)
	}
	want := s.ix.now().Add(arrivalSLA[TriageRed]).Format(time.RFC3339)
	if tasks[0].DueAt != want {
		t.Fatalf("red SLA is 30min: due %s want %s", tasks[0].DueAt, want)
	}
}

func TestGreenTriageOpensNoCase(t *testing.T) {
	s := newTestServer()
	sosIncident(t, s, "+234902", "mild headache")
	if got := len(s.store.ListCases("", "")); got != 0 {
		t.Fatalf("green triage opens no case, got %d", got)
	}
}

func TestOrangeSLAIsFourHours(t *testing.T) {
	s := newTestServer()
	s.ix.now = func() time.Time { return time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC) }
	sosIncident(t, s, "+234903", "deep cut on leg, wound from accident") // trauma => orange
	cases := s.store.ListCases("", "")
	if len(cases) != 1 || cases[0].Priority != TriageOrange {
		t.Fatalf("trauma should open an orange case, got %+v", cases)
	}
	tasks := s.store.ListTasks(cases[0].ID)
	want := s.ix.now().Add(arrivalSLA[TriageOrange]).Format(time.RFC3339)
	if tasks[0].DueAt != want {
		t.Fatalf("orange SLA is 4h: due %s want %s", tasks[0].DueAt, want)
	}
}

func TestContactRollupAndInteractions(t *testing.T) {
	s := newTestServer()
	sosIncident(t, s, "+234904", "chest pain")
	rec, out := getJSON(t, s, "/contacts/+234904")
	if rec.Code != http.StatusOK {
		t.Fatalf("contact lookup: %d", rec.Code)
	}
	contact := out["contact"].(map[string]any)
	if contact["consent_follow_up"] != true {
		t.Fatalf("SOS implies follow-up consent, got %v", contact["consent_follow_up"])
	}
	if contact["interactions"].(float64) != 1 {
		t.Fatalf("one intake = one interaction count, got %v", contact["interactions"])
	}
	inter := out["interactions"].([]any)
	if len(inter) != 1 || inter[0].(map[string]any)["kind"] != "sos" {
		t.Fatalf("interaction log should record the sos, got %v", inter)
	}
	if len(out["cases"].([]any)) != 1 {
		t.Fatalf("contact view should embed the open case, got %v", out["cases"])
	}
}

func TestRepeatSOSDoesNotStackCases(t *testing.T) {
	s := newTestServer()
	sosIncident(t, s, "+234905", "bleeding heavily")
	if rec, _ := post(t, s, "/sos", `{"phone":"+234905","complaint":"bleeding heavily, please hurry"}`); rec.Code != http.StatusOK {
		t.Fatalf("repeat SOS dedupes with 200, got %d", rec.Code)
	}
	if got := len(s.store.ListCases("", "")); got != 1 {
		t.Fatalf("repeat SOS inside window must not stack cases, got %d", got)
	}
}

func TestClosureSchedulesFollowUpOnlyWithConsent(t *testing.T) {
	s := newTestServer()
	s.ix.now = func() time.Time { return time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC) }
	driveToClosed := func(phone string) string {
		inc := sosIncident(t, s, phone, "stroke symptoms, face drooping")
		id := inc["id"].(string)
		for _, to := range []string{StDispatched, StAcked, StOnScene, StTransported, StClosed} {
			rec, _ := post(t, s, "/incidents/"+id+"/transition", `{"to":"`+to+`","actor":"d1"}`)
			if rec.Code != http.StatusOK {
				t.Fatalf("transition to %s: %d", to, rec.Code)
			}
		}
		return id
	}

	// Consenting caller gets a 48h welfare task and the case moves to follow_up.
	driveToClosed("+234906")
	c, _ := s.store.OpenCaseFor("+234906")
	if c.Status != CaseFollowUp {
		t.Fatalf("closed incident moves case to follow_up, got %s", c.Status)
	}
	tasks := s.store.ListTasks(c.ID)
	var fu *CaseTask
	for i := range tasks {
		if tasks[i].Kind == TaskFollowUp48h {
			fu = &tasks[i]
		}
	}
	if fu == nil {
		t.Fatalf("48h follow-up task expected, got %+v", tasks)
	}
	want := s.ix.now().Add(48 * time.Hour).Format(time.RFC3339)
	if fu.DueAt != want {
		t.Fatalf("follow-up due %s want %s", fu.DueAt, want)
	}

	// Caller who revokes consent gets no welfare task.
	sosIncident(t, s, "+234907", "stroke symptoms, face drooping")
	rec, _ := post(t, s, "/contacts/+234907/consent", `{"consent_follow_up":false,"actor":"caller"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("consent revoke: %d", rec.Code)
	}
	driveToClosed2(t, s, "+234907")
	c2, _ := s.store.OpenCaseFor("+234907")
	for _, tk := range s.store.ListTasks(c2.ID) {
		if tk.Kind == TaskFollowUp48h {
			t.Fatalf("revoked consent must block follow-up task, got %+v", tk)
		}
	}
}

func driveToClosed2(t *testing.T, s server, phone string) {
	t.Helper()
	incidents := s.store.List("", "")
	var id string
	for _, i := range incidents {
		if i.CallerPhone == phone && i.Status != StClosed && i.Status != StCancelled {
			id = i.ID
		}
	}
	for _, to := range []string{StDispatched, StAcked, StOnScene, StTransported, StClosed} {
		rec, _ := post(t, s, "/incidents/"+id+"/transition", `{"to":"`+to+`","actor":"d1"}`)
		if rec.Code != http.StatusOK {
			t.Fatalf("transition to %s: %d", to, rec.Code)
		}
	}
}

func TestCaseTransitionRequiresOutcome(t *testing.T) {
	s := newTestServer()
	sosIncident(t, s, "+234908", "chest pain")
	c := s.store.ListCases("", "")[0]
	rec, _ := post(t, s, "/cases/"+c.ID+"/transition", `{"to":"in_progress","actor":"nurse-1"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("open->in_progress legal, got %d", rec.Code)
	}
	rec, _ = post(t, s, "/cases/"+c.ID+"/transition", `{"to":"resolved","actor":"nurse-1"}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("resolve without outcome must 400, got %d", rec.Code)
	}
	rec, _ = post(t, s, "/cases/"+c.ID+"/transition",
		`{"to":"resolved","actor":"nurse-1","outcome":"resolved_care_received"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("resolve with outcome legal, got %d", rec.Code)
	}
	got, _ := s.store.GetCase(c.ID)
	if got.Outcome != OutcomeCareReceived {
		t.Fatalf("outcome not recorded: %q", got.Outcome)
	}
}

func TestTaskLifecycleAndOverdue(t *testing.T) {
	s := newTestServer()
	now := time.Date(2026, 1, 1, 12, 0, 0, 0, time.UTC)
	s.ix.now = func() time.Time { return now }
	sosIncident(t, s, "+234909", "chest pain")
	c := s.store.ListCases("", "")[0]
	tasks := s.store.ListTasks(c.ID)

	// Move the clock past the 30-minute red SLA: the task is overdue.
	s.ix.now = func() time.Time { return now.Add(45 * time.Minute) }
	rec, out := getJSON(t, s, "/tasks/overdue")
	if rec.Code != http.StatusOK {
		t.Fatalf("overdue: %d", rec.Code)
	}
	if len(out["overdue"].([]any)) != 1 {
		t.Fatalf("one overdue arrival-check expected, got %v", out)
	}

	// Mark it done; the overdue list empties.
	rec, _ = post(t, s, "/tasks/"+tasks[0].ID+"/done", `{"note":"facility confirmed arrival"}`)
	if rec.Code != http.StatusOK {
		t.Fatalf("task done: %d", rec.Code)
	}
	_, out = getJSON(t, s, "/tasks/overdue")
	if len(out["overdue"].([]any)) != 0 {
		t.Fatalf("done task must leave the overdue list, got %v", out)
	}
	// Done is terminal.
	rec, _ = post(t, s, "/tasks/"+tasks[0].ID+"/done", `{}`)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("re-done must 400, got %d", rec.Code)
	}
}

func TestCustomTaskAndContactSearch(t *testing.T) {
	s := newTestServer()
	sosIncident(t, s, "+234910", "pregnancy concern, spotting") // orange
	c := s.store.ListCases("", "")[0]
	rec, _ := post(t, s, "/cases/"+c.ID+"/tasks",
		`{"kind":"callback","due_at":"2026-06-01T09:00:00Z","assignee":"midwife-2","note":"ANC booking call"}`)
	if rec.Code != http.StatusCreated {
		t.Fatalf("custom task: %d", rec.Code)
	}
	if got := len(s.store.ListTasks(c.ID)); got != 2 {
		t.Fatalf("arrival-check + callback = 2 tasks, got %d", got)
	}
	rec, out := getJSON(t, s, "/contacts?q=234910")
	if rec.Code != http.StatusOK || len(out["contacts"].([]any)) != 1 {
		t.Fatalf("contact search by phone fragment failed: %d %v", rec.Code, out)
	}
}

func getJSON(t *testing.T, s server, path string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	req := httptest.NewRequest(http.MethodGet, path, nil)
	rec := httptest.NewRecorder()
	s.routes().ServeHTTP(rec, req)
	var out map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &out); err != nil {
		t.Fatalf("json: %v (%s)", err, rec.Body.String())
	}
	return rec, out
}

var _ = strings.TrimSpace
