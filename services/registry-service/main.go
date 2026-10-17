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
	log.Printf("OpenHealth OS registry service on %s (facilities, providers, patients)", addr)
	handler := http.Handler(mux)
	if v := authz.FromEnv(os.Getenv); v != nil {
		handler = v.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}
