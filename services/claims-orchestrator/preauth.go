package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"os/exec"
)

// Preauth answers the claims-gateway /preauth/request operation
// (openapi/claims-gateway.yaml). Same adjudicator seam as claims — a
// preauth that approves what adjudication would deny is a promise the
// scheme cannot keep, so both paths run the same rules.
type PreauthAuthorizer interface {
	Preauth(req PreauthRequest) (map[string]any, error)
}

// PreauthRequest mirrors the gateway contract: member, provider, tariff
// lines, and an optional clinical note for high-cost review.
type PreauthRequest struct {
	MemberID     string `json:"member_id"`
	ProviderID   string `json:"provider_id"`
	Items        []Item `json:"items"`
	ClinicalNote string `json:"clinical_note,omitempty"`
}

// Preauth runs the adjudicator in preauth mode (--preauth).
func (s SubprocessAdjudicator) Preauth(req PreauthRequest) (map[string]any, error) {
	body, _ := json.Marshal(req)
	cmd := exec.Command(s.Python, s.Script, "--preauth")
	cmd.Stdin = bytes.NewReader(body)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("preauth: %v: %s", err, out)
	}
	var m map[string]any
	if err := json.Unmarshal(out, &m); err != nil {
		return nil, fmt.Errorf("preauth decode: %v", err)
	}
	return m, nil
}

type preauthHandler struct {
	Authorizer PreauthAuthorizer
}

func (h preauthHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST only", http.StatusMethodNotAllowed)
		return
	}
	var req PreauthRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.MemberID == "" || len(req.Items) == 0 {
		http.Error(w, "member_id and items required", http.StatusBadRequest)
		return
	}
	res, err := h.Authorizer.Preauth(req)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(res)
}
