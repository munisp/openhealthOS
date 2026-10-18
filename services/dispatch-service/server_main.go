package main

// Entry point: memory store by default, Postgres state plane when
// DATABASE_URL is set (migrations applied out of band by db/migrations).

import (
	"context"
	"log"
	"net/http"
	"os"
)

func main() {
	var store CaseStore = NewMemoryCaseStore()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		pg, err := NewPostgresCaseStore(context.Background(), dsn)
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		store = pg
	}
	addr := os.Getenv("LISTEN")
	if addr == "" {
		addr = ":8093"
	}
	s := server{store: store, ix: newIntegrations(os.Getenv), fleet: NewFleetStore(), mci: NewMCIStore()}
	log.Printf("dispatch-service listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
