package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	addr := ":8085"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	consentURL := os.Getenv("CONSENT_URL")
	var checker ConsentChecker
	if consentURL != "" {
		checker = HTTPConsentChecker{BaseURL: consentURL}
	} else {
		log.Print("CONSENT_URL unset — all cross-facility reads will be denied (fail closed)")
		checker = denyAll{}
	}
	s := server{store: NewStore(checker)}
	log.Printf("OpenHealth OS shr-service on %s (FHIR store + consent enforcement)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}

// denyAll fails closed when no consent service is configured: a missing
// consent check must never become an access grant.
type denyAll struct{}

func (denyAll) Check(_, _, _ string) (bool, error) { return false, nil }
