package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"
)

func main() {
	addr := ":8085"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	// Consent seam: CONSENT_URL wires the consent service; unset fails
	// closed — a missing consent check must never become an access grant.
	consentURL := os.Getenv("CONSENT_URL")
	var checker ConsentChecker
	if consentURL != "" {
		checker = HTTPConsentChecker{BaseURL: consentURL}
	} else {
		log.Print("CONSENT_URL unset — all cross-facility reads will be denied (fail closed)")
		checker = denyAll{}
	}
	// Storage seam: DATABASE_URL binds Postgres (resources + audit survive
	// restarts); unset keeps the in-memory backend (edge, dev, tests).
	var be backend = newMemoryBackend()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pg, err := newPostgresBackend(ctx, dsn)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		be = pg
		log.Print("shr storage: postgres (state plane)")
	} else {
		log.Print("shr storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{store: NewStoreWithBackend(be, checker)}
	handler := http.Handler(s.routes())
	if v := authz.FromEnv(os.Getenv); v != nil {
		handler = v.Middleware(handler)
		log.Print("oidc: enforcing bearer auth (claims carry facility attribution)")
	}
	log.Printf("OpenHealth OS shr-service on %s (FHIR store + consent enforcement)", addr)
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}

// denyAll fails closed when no consent service is configured: a missing
// consent check must never become an access grant.
type denyAll struct{}

func (denyAll) Check(_, _, _ string) (bool, error) { return false, nil }
