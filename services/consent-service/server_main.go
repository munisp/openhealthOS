package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	addr := ":8082"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	key := []byte(os.Getenv("CONSENT_HMAC_KEY"))
	if len(key) == 0 {
		key = []byte("dev-only-key-not-for-production")
		log.Print("WARNING: CONSENT_HMAC_KEY unset — using dev key")
	}
	// Storage seam: DATABASE_URL binds Postgres (artefacts + audit survive
	// restarts); unset keeps the in-memory manager (edge, dev, tests).
	var svc ConsentService = NewManager(key)
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pg, err := NewPostgresManager(ctx, dsn, key)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		svc = pg
		log.Print("consent storage: postgres (state plane)")
	} else {
		log.Print("consent storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{m: svc}
	log.Printf("OpenHealth OS consent-service on %s (HIE-CM pattern)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
