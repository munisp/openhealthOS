package main

import (
	"context"
	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	addr := ":8081"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	// Storage seam: DATABASE_URL binds Postgres (state plane); unset keeps
	// the in-memory store (edge nodes, local dev, tests).
	var store Store = NewMemoryStore()
	var patients PatientStore = NewMemoryPatientStore()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pg, err := NewPostgresStore(ctx, dsn)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		store, patients = pg, NewPostgresPatientStore(pg.pool)
		log.Print("registry storage: postgres (state plane)")
	} else {
		log.Print("registry storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{store: store}
	mux := s.routes()
	registerPatientRoutes(mux, patients)
	// KYC seams (ADR-0012): NIN_VERIFY_URL binds a NIMC-compatible
	// verification endpoint, otherwise verification defers offline;
	// SMS_WEBHOOK_URL binds the OTP channel, otherwise codes are logged.
	var verifier Verifier = OfflineVerifier{}
	if u := os.Getenv("NIN_VERIFY_URL"); u != "" {
		verifier = HTTPVerifier{URL: u, Token: os.Getenv("NIN_VERIFY_TOKEN")}
		log.Print("kyc: NIN verification via NIN_VERIFY_URL")
	} else {
		log.Print("kyc: offline verifier — NIN checks defer until configured")
	}
	var sender OTPSender = LogSender{Logf: log.Printf}
	if u := os.Getenv("SMS_WEBHOOK_URL"); u != "" {
		sender = WebhookSender{URL: u, Token: os.Getenv("SMS_WEBHOOK_TOKEN")}
		log.Print("kyc: OTP via SMS webhook")
	}
	registerKYCRoutes(mux, patients, verifier, sender)
	log.Printf("OpenHealth OS registry service on %s (facilities, providers, patients)", addr)
	handler := http.Handler(mux)
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		log.Fatal(authErr)
	}
	if authV != nil {
		handler = authV.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}
