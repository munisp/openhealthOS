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
	Step   string // "menu" | "eligibility-member" | "enrol-member" | "feed-patient" | "wallet-nin" | "wallet-pay-facility" | "wallet-pay-amount"
	Buffer string
	Phone  string // msisdn from the telco — the patient's second factor
}

// Gateway drives USSD sessions.
type Gateway struct {
	mu       sync.Mutex
	sessions map[string]*Session
	checker  EligibilityChecker
	reader   FeedReader
	wallet   WalletReader // ADR-0030 citizen health wallet (nil = menu hides it)
	payer    WalletPayer
}

func NewGateway(c EligibilityChecker) *Gateway {
	return &Gateway{sessions: map[string]*Session{}, checker: c, reader: EmptyFeedReader{}}
}

// NewGatewayWithFeed wires the patient read channel (ADR-0013).
func NewGatewayWithFeed(c EligibilityChecker, r FeedReader) *Gateway {
	g := NewGateway(c)
	if r != nil {
		g.reader = r
	}
	return g
}

// WithWallet wires the citizen health wallet (ADR-0030).
func (g *Gateway) WithWallet(r WalletReader, p WalletPayer) *Gateway {
	g.wallet = r
	g.payer = p
	return g
}

const menu = "CON OpenHealth\n1. Check my coverage\n2. My enrolment ID\n3. My clinic updates\n4. My health wallet\n0. Exit"

// Handle processes one USSD step with no caller identity (tests, legacy
// aggregators); HandleWithPhone is the production path.
func (g *Gateway) Handle(sessionID, text string) string {
	return g.HandleWithPhone(sessionID, "", text)
}

// HandleWithPhone processes one USSD step. text is the full cumulative
// input string (e.g. "1*NG-0001") per telco convention; phone is the
// telco-supplied msisdn. Returns CON (continue) / END (terminate) replies.
func (g *Gateway) HandleWithPhone(sessionID, phone, text string) string {
	g.mu.Lock()
	defer g.mu.Unlock()
	s, ok := g.sessions[sessionID]
	if !ok {
		s = &Session{Step: "menu"}
		g.sessions[sessionID] = s
	}
	if phone != "" {
		s.Phone = phone
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
		case "3":
			s.Step = "feed-patient"
			return "CON Enter your patient code (on your clinic card):"
		case "4":
			if g.wallet == nil {
				delete(g.sessions, sessionID)
				return "END Health wallet is not active in this state yet. Ask at your PHC."
			}
			s.Step = "wallet-nin"
			return "CON Enter your NIN (the number on your NIN slip):"
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
	case "feed-patient":
		phone := s.Phone
		delete(g.sessions, sessionID)
		if phone == "" {
			return "END This network did not send your number. Use the patient app or ask at your PHC."
		}
		items, err := g.reader.Feed(last, phone)
		if err != nil {
			// Never reveal which factor failed (no enumeration, same as the
			// web portal); a transient error reads identically to no feed.
			return "END No clinic updates found for that code on this number. Check the code on your clinic card."
		}
		return ussdFeed(items)
	case "wallet-nin":
		// Balance first; the same step arms the pay flow.
		total, envs, err := g.wallet.Balance(last)
		if err != nil {
			delete(g.sessions, sessionID)
			return "END No health wallet found for that NIN. Ask at your PHC or SHIA office."
		}
		s.Buffer = last
		s.Step = "wallet-pay-facility"
		naira := fmt.Sprintf("%.2f", float64(total)/100)
		var extra string
		for name, bal := range envs {
			if name != "GENERAL" {
				extra += fmt.Sprintf(" (%s: ₦%.2f)", name, float64(bal)/100)
			}
		}
		return fmt.Sprintf("CON Health wallet: ₦%s%s\nPay a facility: enter facility code (e.g. phc-karu)\n0. Exit", naira, extra)
	case "wallet-pay-facility":
		if last == "0" {
			delete(g.sessions, sessionID)
			return "END Thank you."
		}
		s.Step = "wallet-pay-amount"
		s.Buffer += "|" + last // nin|facility
		return "CON Enter amount in naira:"
	case "wallet-pay-amount":
		delete(g.sessions, sessionID)
		if g.payer == nil {
			return "END Payments are not active in this state yet. Your balance is safe."
		}
		ninFac := strings.SplitN(s.Buffer, "|", 2)
		if len(ninFac) != 2 {
			return "END Session error. Start again."
		}
		var naira int
		if _, err := fmt.Sscanf(last, "%d", &naira); err != nil || naira <= 0 {
			return "END Invalid amount. Dial again to retry."
		}
		// Deterministic ref: session + step count — a redial of the same
		// session can never double-pay (wallet-service dedupes).
		ref := "USSD-" + sessionID
		if err := g.payer.Pay(ninFac[0], "GENERAL", "WALLET-"+ninFac[1], naira*100, ref); err != nil {
			return "END Payment failed: funds can only pay registered healthcare providers. Your money is safe."
		}
		return fmt.Sprintf("END Paid ₦%d to %s. Keep this screen as your receipt.", naira, ninFac[1])
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
		w.Write([]byte(s.g.HandleWithPhone(req.SessionID, req.MSISDN, req.Text)))
	})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	return mux
}
