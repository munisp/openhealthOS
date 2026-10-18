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
	s := server{g: NewGatewayWithFeed(staticChecker{}, reader)}
	log.Printf("OpenHealth OS ussd-gateway on %s (fixture checker — bind the eligibility seam in production)", addr)
	log.Fatal(servex.ListenAndServe(addr, s.routes(), servex.Options{}))
}
