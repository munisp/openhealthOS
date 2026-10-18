package main

// onboarding.go — staff onboarding workflows (ADR-0020, docs/roles.md).
//
// Every staff role walks the same five-step chain: invite →
// identity-check → credential-verify → facility-assign → activate.
// Which steps a role actually requires is data (RoleMatrix) — a
// pharmacist cannot activate without a verified PCN licence, a
// dispatcher has no statutory licence and skips that step, but nobody
// skips identity and nobody onboards anonymously (ADR-0018: state
// changes name who made them). Suspension says why and never deletes.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"sync"
	"time"
)

// Onboarding steps, in order.
const (
	StepInvite           = "invite"
	StepIdentityCheck    = "identity-check"
	StepCredentialVerify = "credential-verify"
	StepFacilityAssign   = "facility-assign"
	StepActivate         = "activate"
)

var stepOrder = []string{StepInvite, StepIdentityCheck, StepCredentialVerify,
	StepFacilityAssign, StepActivate}

// RoleMatrix: role → the steps it requires. Data, not code — a state
// extends the catalogue without touching the workflow engine.
var RoleMatrix = map[string][]string{
	"physician":             stepOrder,
	"nurse":                 stepOrder,
	"midwife":               stepOrder,
	"pharmacist":            stepOrder,
	"lab-scientist":         stepOrder,
	"chew":                  stepOrder,
	"dispatcher":            {StepInvite, StepIdentityCheck, StepFacilityAssign, StepActivate},
	"paramedic":             {StepInvite, StepIdentityCheck, StepFacilityAssign, StepActivate},
	"facility-admin":        {StepInvite, StepIdentityCheck, StepFacilityAssign, StepActivate},
	"lga-officer":           {StepInvite, StepIdentityCheck, StepFacilityAssign, StepActivate},
	"state-program-officer": {StepInvite, StepIdentityCheck, StepFacilityAssign, StepActivate},
	"claims-officer":        {StepInvite, StepIdentityCheck, StepFacilityAssign, StepActivate},
	"auditor":               {StepInvite, StepIdentityCheck, StepFacilityAssign, StepActivate},
}

// Onboarding is one invite's journey through the chain.
type Onboarding struct {
	ID          string          `json:"id"`
	Role        string          `json:"role"`
	Name        string          `json:"name"`
	Phone       string          `json:"phone"`
	License     string          `json:"license,omitempty"`
	FacilityID  string          `json:"facility_id,omitempty"`
	Status      string          `json:"status"` // in-progress | active | suspended
	CurrentStep string          `json:"current_step"`
	Timeline    []TimelineEntry `json:"timeline"`
}

// TimelineEntry mirrors the incident/case pattern (dispatch-service).
type TimelineEntry struct {
	TS     string `json:"ts"`
	Actor  string `json:"actor"`
	Action string `json:"action"`
	Detail string `json:"detail"`
}

type onboardingStore struct {
	mu   sync.Mutex
	rows map[string]Onboarding
	seq  int
}

func newOnboardingStore() *onboardingStore {
	return &onboardingStore{rows: map[string]Onboarding{}}
}

func requiredSteps(role string) ([]string, bool) {
	steps, ok := RoleMatrix[role]
	return steps, ok
}

func nextStep(role, current string) (string, bool) {
	steps, ok := requiredSteps(role)
	if !ok {
		return "", false
	}
	for i, s := range steps {
		if s == current {
			if i+1 < len(steps) {
				return steps[i+1], true
			}
			return "", true // chain complete
		}
	}
	return "", false
}

var errUnknownRole = fmt.Errorf("unknown role — see docs/roles.md")

func (o *onboardingStore) invite(role, name, phone, actor string) (Onboarding, error) {
	steps, ok := requiredSteps(role)
	if !ok {
		return Onboarding{}, errUnknownRole
	}
	if actor == "" || name == "" || phone == "" {
		return Onboarding{}, fmt.Errorf("name, phone and actor are required")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	o.seq++
	now := time.Now().UTC().Format(time.RFC3339)
	ob := Onboarding{
		ID: fmt.Sprintf("ONB-%05d", o.seq), Role: role, Name: name, Phone: phone,
		Status: "in-progress", CurrentStep: steps[0],
		Timeline: []TimelineEntry{{TS: now, Actor: actor, Action: StepInvite,
			Detail: "invited as " + role}},
	}
	// invite completes immediately on creation; advance the pointer
	if nxt, _ := nextStep(role, StepInvite); nxt != "" {
		ob.CurrentStep = nxt
	} else {
		ob.Status = "active"
	}
	o.rows[ob.ID] = ob
	return ob, nil
}

func (o *onboardingStore) advance(id, actor, detail string, mutate func(*Onboarding)) (Onboarding, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	ob, ok := o.rows[id]
	if !ok {
		return Onboarding{}, ErrNotFound
	}
	if ob.Status != "in-progress" {
		return Onboarding{}, fmt.Errorf("%w: onboarding is %s", errBadState, ob.Status)
	}
	if mutate != nil {
		mutate(&ob)
	}
	nxt, _ := nextStep(ob.Role, ob.CurrentStep)
	now := time.Now().UTC().Format(time.RFC3339)
	ob.Timeline = append(ob.Timeline, TimelineEntry{
		TS: now, Actor: actor, Action: ob.CurrentStep, Detail: detail})
	if nxt == "" {
		ob.Status = "active"
		ob.CurrentStep = ""
		ob.Timeline = append(ob.Timeline, TimelineEntry{
			TS: now, Actor: actor, Action: StepActivate, Detail: "account live"})
	} else {
		ob.CurrentStep = nxt
	}
	o.rows[id] = ob
	return ob, nil
}

var errBadState = fmt.Errorf("bad onboarding state")

func (o *onboardingStore) setStatus(id, status, actor, reason string) (Onboarding, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	ob, ok := o.rows[id]
	if !ok {
		return Onboarding{}, ErrNotFound
	}
	if status != "suspended" && status != "active" {
		return Onboarding{}, fmt.Errorf("status must be suspended or active")
	}
	if reason == "" || actor == "" {
		return Onboarding{}, fmt.Errorf("status changes name who and why")
	}
	ob.Status = status
	ob.Timeline = append(ob.Timeline, TimelineEntry{
		TS: time.Now().UTC().Format(time.RFC3339), Actor: actor,
		Action: status, Detail: reason})
	o.rows[id] = ob
	return ob, nil
}

func (o *onboardingStore) get(id string) (Onboarding, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if ob, ok := o.rows[id]; ok {
		return ob, nil
	}
	return Onboarding{}, ErrNotFound
}

func (o *onboardingStore) list(status, role string) []Onboarding {
	o.mu.Lock()
	defer o.mu.Unlock()
	out := []Onboarding{}
	for _, ob := range o.rows {
		if status != "" && ob.Status != status {
			continue
		}
		if role != "" && ob.Role != role {
			continue
		}
		out = append(out, ob)
	}
	sort.Slice(out, func(a, b int) bool { return out[a].ID < out[b].ID })
	return out
}

// -------------------------------------------------------------- HTTP glue

func (s server) onboardingRoutes(mux *http.ServeMux) {
	mux.HandleFunc("GET /onboarding/roles", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, 200, map[string]any{"roles": RoleMatrix})
	})
	mux.HandleFunc("POST /onboarding", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Role, Name, Phone, Actor string
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		ob, err := s.onboarding.invite(req.Role, req.Name, req.Phone, req.Actor)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, 201, ob)
	})
	mux.HandleFunc("GET /onboarding", func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		writeJSON(w, 200, map[string]any{
			"onboardings": s.onboarding.list(q.Get("status"), q.Get("role"))})
	})
	mux.HandleFunc("GET /onboarding/{id}", func(w http.ResponseWriter, r *http.Request) {
		ob, err := s.onboarding.get(r.PathValue("id"))
		if err != nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		writeJSON(w, 200, ob)
	})
	mux.HandleFunc("POST /onboarding/{id}/advance", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Actor      string `json:"actor"`
			Detail     string `json:"detail"`
			License    string `json:"license"`
			FacilityID string `json:"facility_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Actor == "" {
			http.Error(w, "actor required", http.StatusBadRequest)
			return
		}
		ob, err := s.onboarding.advance(r.PathValue("id"), req.Actor, req.Detail,
			func(o *Onboarding) {
				if req.License != "" {
					o.License = req.License
				}
				if req.FacilityID != "" {
					o.FacilityID = req.FacilityID
				}
			})
		if err != nil {
			code := http.StatusBadRequest
			if err == ErrNotFound {
				code = http.StatusNotFound
			}
			http.Error(w, err.Error(), code)
			return
		}
		writeJSON(w, 200, ob)
	})
	mux.HandleFunc("POST /onboarding/{id}/status", func(w http.ResponseWriter, r *http.Request) {
		var req struct {
			Status, Actor, Reason string
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad json", http.StatusBadRequest)
			return
		}
		ob, err := s.onboarding.setStatus(r.PathValue("id"), req.Status, req.Actor, req.Reason)
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		writeJSON(w, 200, ob)
	})
}
