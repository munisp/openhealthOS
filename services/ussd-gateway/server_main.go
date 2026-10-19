package main

import (
	"github.com/munisp/openhealthOS/shared/servex"
	"log"
	"os"
)

// staticChecker is the fixture seam: production answers from the
// adjudicator/registry over the eligibility contract (openapi/).
type staticChecker struct{}

func (staticChecker) Check(id string) (bool, string, string, error) {
	if id == "" {
		return false, "", "", errUnknown{}
	}
	return true, "STATE-BASIC", "2027-12-31", nil // fixture
}

type errUnknown struct{}

func (errUnknown) Error() string { return "unknown member" }

func main() {
	addr := ":8083"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	// Patient read channel: NOTIFY_URL binds the notification service;
	// unset = empty-feed stub (enrolment/eligibility still work).
	var reader FeedReader = EmptyFeedReader{}
	if u := os.Getenv("NOTIFY_URL"); u != "" {
		reader = HTTPFeedReader{BaseURL: u}
		log.Print("ussd: patient updates via NOTIFY_URL")
	}
	g := NewGatewayWithFeed(staticChecker{}, reader)
	// Citizen health wallet (ADR-0030): WALLET_URL binds wallet-service;
	// unset = wallet menu answers "not active yet".
	if u := os.Getenv("WALLET_URL"); u != "" {
		hw := HTTPWallet{BaseURL: u}
		g.WithWallet(hw, hw)
		log.Print("ussd: health wallet via WALLET_URL")
	}
	// Benefit visibility (ADR-0031): CLAIMS_URL binds claims-orchestrator.
	if u := os.Getenv("CLAIMS_URL"); u != "" {
		g.WithBenefits(HTTPBenefits{BaseURL: u})
		log.Print("ussd: benefit visibility via CLAIMS_URL")
	}
	s := server{g: g}
	log.Printf("OpenHealth OS ussd-gateway on %s (fixture checker — bind the eligibility seam in production)", addr)
	log.Fatal(servex.ListenAndServe(addr, s.routes(), servex.Options{}))
}
