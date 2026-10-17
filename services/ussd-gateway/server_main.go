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
	s := server{g: NewGateway(staticChecker{})}
	log.Printf("OpenHealth OS ussd-gateway on %s (fixture checker — bind the eligibility seam in production)", addr)
	log.Fatal(servex.ListenAndServe(addr, s.routes(), servex.Options{}))
}
