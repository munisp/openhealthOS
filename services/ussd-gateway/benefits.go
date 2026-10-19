package main

// Benefit visibility over USSD (ADR-0031): "what does my cover include?"
// answered by the same catalogue the adjudicator enforces. The gateway
// resolves the member's scheme via the eligibility seam, then reads the
// plain-language summary from claims-orchestrator's benefits endpoint.

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// BenefitsReader fetches the scheme summary.
type BenefitsReader interface {
	Explain(scheme string) (headline string, referralNote string, err error)
}

// HTTPBenefits binds claims-orchestrator /benefits/{scheme}.
type HTTPBenefits struct {
	BaseURL string
	Client  *http.Client
}

func (h HTTPBenefits) http() *http.Client {
	if h.Client != nil {
		return h.Client
	}
	return &http.Client{Timeout: 8 * time.Second}
}

func (h HTTPBenefits) Explain(scheme string) (string, string, error) {
	resp, err := h.http().Get(strings.TrimRight(h.BaseURL, "/") +
		"/benefits/" + url.PathEscape(scheme))
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return "", "", fmt.Errorf("benefits: HTTP %d", resp.StatusCode)
	}
	var body struct {
		Headline     string `json:"headline"`
		ReferralNote string `json:"referral_note"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return "", "", err
	}
	return body.Headline, body.ReferralNote, nil
}
