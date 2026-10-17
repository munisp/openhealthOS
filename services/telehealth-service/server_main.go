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
	addr := ":8087"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	// Storage seam: DATABASE_URL binds Postgres (state plane); unset keeps
	// the in-memory service (edge, dev, tests).
	var svc TelehealthService = NewService()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pg, err := NewPostgresService(ctx, dsn)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		svc = pg
		log.Print("telehealth storage: postgres (state plane)")
	} else {
		log.Print("telehealth storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{svc: svc}
	log.Printf("OpenHealth OS telehealth-service on %s (nurse-first consults + referrals)", addr)
	handler := http.Handler(s.routes())
	if v := authz.FromEnv(os.Getenv); v != nil {
		handler = v.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}
