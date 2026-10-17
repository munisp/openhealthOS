// Package main — OpenHealth OS USSD gateway (ADR-0001 fallback channel).
//
// Feature phones carry enrolment and eligibility where data is absent.
// A USSD session is a state machine over the gateway's text input; the
// eligibility question is answered by the same eligibility seam the claims
// gateway uses, so the answer never diverges by channel.
package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"sync"
)

// EligibilityChecker answers the one question patients ask most:
// am I covered, and until when? Implemented by the adjudicator/registry seam.
type EligibilityChecker interface {
	Check(memberID string) (eligible bool, scheme string, validUntil string, err error)
}

// Session holds one USSD dialog's position in the menu tree.
type Session struct {
	Step   string // "menu" | "eligibility-member" | "enrol-member"
	Buffer string
}

// Gateway drives USSD sessions.
type Gateway struct {
	mu       sync.Mutex
	sessions map[string]*Session
	checker  EligibilityChecker
}

func NewGateway(c EligibilityChecker) *Gateway {
	return &Gateway{sessions: map[string]*Session{}, checker: c}
}

const menu = "CON OpenHealth\n1. Check my coverage\n2. My enrolment ID\n0. Exit"

// Handle processes one USSD step. text is the full cumulative input string
// (e.g. "1*NG-0001") per telco convention; returns the reply with the
// CON (continue) / END (terminate) prefix.
func (g *Gateway) Handle(sessionID, text string) string {
	g.mu.Lock()
	defer g.mu.Unlock()
	s, ok := g.sessions[sessionID]
	if !ok {
		s = &Session{Step: "menu"}
		g.sessions[sessionID] = s
	}
	if text == "" {
		s.Step = "menu"
		return menu
	}
	parts := strings.Split(text, "*")
	last := parts[len(parts)-1]

	switch s.Step {
	case "menu":
		switch last {
		case "1":
			s.Step = "eligibility-member"
			return "CON Enter your member ID (e.g. NG-0001 or OH-...):"
		case "2":
			s.Step = "enrol-member"
			return "CON Enter the phone or ID you enrolled with:"
		case "0":
			delete(g.sessions, sessionID)
			return "END Thank you."
		default:
			return "CON Invalid choice.\n" + strings.TrimPrefix(menu, "CON ")
		}
	case "eligibility-member":
		delete(g.sessions, sessionID)
		eligible, scheme, validUntil, err := g.checker.Check(last)
		if err != nil {
			return "END We could not find that member ID. Visit your PHC to enrol."
		}
		if !eligible {
			return "END Coverage is INACTIVE for " + last + ". Visit your PHC or SHIA office."
		}
		return fmt.Sprintf("END COVERED under %s until %s. Show this at any OpenHealth facility.", scheme, validUntil)
	case "enrol-member":
		delete(g.sessions, sessionID)
		return "END Your enrolment is linked to " + last + ". Your care team can confirm at your next visit."
	}
	delete(g.sessions, sessionID)
	return "END Session ended."
}

// ---- HTTP surface (telco aggregator callback shape) ----

type server struct{ g *Gateway }

func (s server) routes() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/ussd", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			SessionID string `json:"session_id"`
			MSISDN    string `json:"msisdn"`
			Text      string `json:"text"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "bad request: "+err.Error(), http.StatusBadRequest)
			return
		}
		if req.SessionID == "" {
			http.Error(w, "session_id required", http.StatusBadRequest)
			return
		}
		w.Header().Set("Content-Type", "text/plain")
		w.Write([]byte(s.g.Handle(req.SessionID, req.Text)))
	})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}
