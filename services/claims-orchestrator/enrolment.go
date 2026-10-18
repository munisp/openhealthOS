package main

// Enrolment endpoint (ADR-0017): the coverage registry grows an API.
// Same subprocess seam as adjudication — one adjudicator, one member
// store (MEMBERS_FILE), whether the writer is this API, a state sync,
// or a USSD session.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os/exec"
)

// Enroller runs the adjudicator in enrolment/suspend modes.
type Enroller interface {
	Enrol(record json.RawMessage) (map[string]any, error)
	Suspend(memberID, reason string) (map[string]any, error)
}

func (s SubprocessAdjudicator) runMode(mode string, stdin []byte) (map[string]any, error) {
	cmd := exec.Command(s.Python, s.Script, mode)
	cmd.Stdin = bytes.NewReader(stdin)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("enrolment %s: %v: %s", mode, err, out)
	}
	var m map[string]any
	if err := json.Unmarshal(out, &m); err != nil {
		return nil, fmt.Errorf("enrolment decode: %v", err)
	}
	return m, nil
}

func (s SubprocessAdjudicator) Enrol(record json.RawMessage) (map[string]any, error) {
	return s.runMode("--enrol", record)
}

func (s SubprocessAdjudicator) Suspend(memberID, reason string) (map[string]any, error) {
	body, _ := json.Marshal(map[string]string{"member_id": memberID, "reason": reason})
	return s.runMode("--suspend", body)
}

type enrolmentHandler struct {
	Enroller Enroller
}

func (h enrolmentHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read body: "+err.Error(), http.StatusBadRequest)
		return
	}
	var res map[string]any
	// Suspend rides the same endpoint with an explicit action — a state
	// change this sensitive is always verb-first, never implicit.
	var probe struct {
		Action   string `json:"action"`
		MemberID string `json:"member_id"`
		Reason   string `json:"reason"`
	}
	if err := json.Unmarshal(body, &probe); err != nil {
		http.Error(w, "bad json: "+err.Error(), http.StatusBadRequest)
		return
	}
	if probe.Action == "suspend" {
		res, err = h.Enroller.Suspend(probe.MemberID, probe.Reason)
	} else {
		res, err = h.Enroller.Enrol(body)
	}
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest) // validation errors are 4xx — the caller fixes the record
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(res)
}
