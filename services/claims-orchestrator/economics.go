package main

// Provider economics seam (ADR-0032): margin-per-code cost accounting,
// capitation panel P&L, denial analytics, and tariff simulation via a
// subprocess into packages/economics (same doctrine as the adjudicator
// and benefits seams). Money figures are kobo; reports carry the tariff
// vintage year so a stale schedule is visible in every output.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os/exec"
)

// EconomicsRunner executes one economics op against the package.
type EconomicsRunner interface {
	Run(op map[string]any) (map[string]any, error)
}

// SubprocessEconomics shells out to packages/economics/economics.py.
type SubprocessEconomics struct {
	Python string
	Script string
}

func (s SubprocessEconomics) Run(op map[string]any) (map[string]any, error) {
	body, _ := json.Marshal(op)
	cmd := exec.Command(s.Python, s.Script)
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("economics: %v: %s", err, out)
	}
	var d map[string]any
	if err := json.Unmarshal(out, &d); err != nil {
		return nil, fmt.Errorf("economics decode: %v", err)
	}
	if e, ok := d["error"]; ok {
		return nil, fmt.Errorf("economics: %v", e)
	}
	return d, nil
}

// economicsHandler serves POST /economics/{op} with the op payload as
// body — margins | panel | denials | tariff-sim.
type economicsHandler struct{ R EconomicsRunner }

var economicsOps = map[string]bool{
	"margins": true, "panel": true, "denials": true, "tariff-sim": true,
}

func (h economicsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"POST only"}`, http.StatusMethodNotAllowed)
		return
	}
	op := r.PathValue("op")
	if !economicsOps[op] {
		http.Error(w, `{"error":"unknown economics op"}`, http.StatusNotFound)
		return
	}
	var payload map[string]any
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		http.Error(w, `{"error":"bad json"}`, http.StatusBadRequest)
		return
	}
	payload["op"] = op
	out, err := h.R.Run(payload)
	if err != nil {
		w.WriteHeader(http.StatusUnprocessableEntity)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(out)
}
