package main

// Benefits seam (ADR-0031): the benefit package as data, patient-facing.
// One subprocess contract into packages/benefits (same doctrine as the
// adjudicator seam) — the catalogue the patient reads is the catalogue
// the adjudicator enforces.

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os/exec"
	"strconv"
)

// BenefitQuerier answers coverage questions for any channel.
type BenefitQuerier interface {
	Explain(scheme string) (map[string]any, error)
	Check(scheme, code string) (map[string]any, error)
	Staleness(targetYear int, cpiPath map[string]float64) (map[string]any, error)
}

// SubprocessBenefits shells out to packages/benefits/benefits.py.
type SubprocessBenefits struct {
	Python string
	Script string
}

func (s SubprocessBenefits) call(op map[string]any) (map[string]any, error) {
	body, _ := json.Marshal(op)
	cmd := exec.Command(s.Python, s.Script)
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("benefits: %v: %s", err, out)
	}
	var d map[string]any
	if err := json.Unmarshal(out, &d); err != nil {
		return nil, fmt.Errorf("benefits decode: %v", err)
	}
	if e, ok := d["error"]; ok {
		return nil, fmt.Errorf("benefits: %v", e)
	}
	return d, nil
}

func (s SubprocessBenefits) Explain(scheme string) (map[string]any, error) {
	return s.call(map[string]any{"op": "explain", "scheme": scheme})
}

func (s SubprocessBenefits) Check(scheme, code string) (map[string]any, error) {
	return s.call(map[string]any{"op": "check", "scheme": scheme, "code": code})
}

func (s SubprocessBenefits) Staleness(targetYear int, cpiPath map[string]float64) (map[string]any, error) {
	return s.call(map[string]any{"op": "staleness", "target_year": targetYear, "cpi_path": cpiPath})
}

// benefitsHandler serves the patient-facing benefit queries.
type benefitsHandler struct{ Q BenefitQuerier }

func (h benefitsHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	scheme := r.PathValue("scheme")
	w.Header().Set("Content-Type", "application/json")
	switch {
	case r.URL.Query().Get("code") != "":
		out, err := h.Q.Check(scheme, r.URL.Query().Get("code"))
		respondBenefit(w, out, err)
	case r.URL.Query().Get("staleness_year") != "":
		year, err := strconv.Atoi(r.URL.Query().Get("staleness_year"))
		if err != nil {
			http.Error(w, `{"error":"bad year"}`, 400)
			return
		}
		cpi := map[string]float64{}
		json.Unmarshal([]byte(r.URL.Query().Get("cpi_path")), &cpi) // optional JSON {"2026":0.15}
		out, err := h.Q.Staleness(year, cpi)
		respondBenefit(w, out, err)
	default:
		out, err := h.Q.Explain(scheme)
		respondBenefit(w, out, err)
	}
}

func respondBenefit(w http.ResponseWriter, out map[string]any, err error) {
	if err != nil {
		w.WriteHeader(http.StatusUnprocessableEntity)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(out)
}
