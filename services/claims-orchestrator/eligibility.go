package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os/exec"
)

// Eligibility answers the claims-gateway /eligibility/check operation
// (openapi/claims-gateway.yaml). Same seam as adjudication — one answer,
// whether the question arrives via app, API, or USSD.
type EligibilityChecker interface {
	CheckEligibility(memberID, serviceDate string) (map[string]any, error)
}

// CheckEligibility runs the adjudicator in eligibility mode (--check).
func (s SubprocessAdjudicator) CheckEligibility(memberID, serviceDate string) (map[string]any, error) {
	body, _ := json.Marshal(map[string]string{"member_id": memberID, "service_date": serviceDate})
	cmd := exec.Command(s.Python, s.Script, "--check")
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("eligibility: %v: %s", err, out)
	}
	var m map[string]any
	if err := json.Unmarshal(out, &m); err != nil {
		return nil, fmt.Errorf("eligibility decode: %v", err)
	}
	return m, nil
}

type eligibilityHandler struct {
	Checker EligibilityChecker
}

func (h eligibilityHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req struct {
		MemberID    string `json:"member_id"`
		ServiceDate string `json:"service_date"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.MemberID == "" {
		http.Error(w, "member_id required", http.StatusBadRequest)
		return
	}
	res, err := h.Checker.CheckEligibility(req.MemberID, req.ServiceDate)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(res)
}
