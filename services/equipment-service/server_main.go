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
	addr := ":8086"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	// Storage seam: DATABASE_URL binds Postgres (state plane); unset keeps
	// the in-memory service (edge, dev, tests).
	var svc EquipmentService = NewService()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pg, err := NewPostgresService(ctx, dsn)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		svc = pg
		log.Print("equipment storage: postgres (state plane)")
	} else {
		log.Print("equipment storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{svc: svc}
	log.Printf("OpenHealth OS equipment-service on %s (registry + BMET ticket routing)", addr)
	handler := http.Handler(s.routes())
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		log.Fatal(authErr)
	}
	if authV != nil {
		handler = authV.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.InstrumentedListenAndServe("equipment-service", os.Getenv, addr, handler))
}
