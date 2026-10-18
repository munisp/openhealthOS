package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/rebac"
	"github.com/munisp/openhealthOS/shared/sealx"
	"github.com/munisp/openhealthOS/shared/servex"
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
	// Biometrics (ADR-0017): anti-gaming identity assurance. Templates are
	// sealed at rest via the sealx keyring; SEALX_KEY unset keeps the
	// loud-plaintext dev mode (the stored sentinel makes that visible).
	// BIOMETRIC_THRESHOLD (default 0.85) is the one policy knob.
	var bioStore BiometricStore = NewMemoryBiometricStore()
	if pgStore, ok := store.(*PostgresStore); ok {
		bioStore = NewPostgresBiometricStore(pgStore.pool)
	}
	sealer, err := sealx.FromEnv(os.Getenv)
	if err != nil {
		log.Fatalf("sealx: %v", err)
	}
	if sealer == nil {
		log.Print("biometrics: SEALX_KEY unset — templates stored PLAIN (dev only)")
	}
	registerBiometricRoutes(mux, NewBiometricEngine(bioStore, patients, sealer, os.Getenv))
	log.Printf("biometrics: matcher=SimMatcher threshold=%.2f (set BIOMETRIC_THRESHOLD)", matchThreshold(os.Getenv))
	// ReBAC (ADR-0015): tuple store on the same persistence seam as the
	// registry itself; the patient-read gate binds only when
	// REBAC_ENFORCE=true (state plane closed, edge open by default).
	var rebacStore rebac.Store = rebac.NewMemoryStore()
	if pgStore, ok := store.(*PostgresStore); ok {
		rebacStore = rebac.NewPostgresStoreFromPool(pgStore.pool)
	}
	checker := &rebac.Checker{Store: rebacStore}
	rbac := newRebacAPI(checker, patients, strings.Split(os.Getenv("REBAC_BOOTSTRAP_ADMINS"), ","))
	rbac.registerRoutes(mux)
	log.Printf("OpenHealth OS registry service on %s (facilities, providers, patients)", addr)
	handler := http.Handler(mux)
	if os.Getenv("REBAC_ENFORCE") == "true" {
		handler = rbac.gate(handler)
		log.Print("rebac: patient reads gated (X-User-ID + grants required)")
	}
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
