package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"
)

func main() {
	addr := ":8443"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	// Storage seam: DATABASE_URL binds Postgres — exactly-once ingest that
	// survives restarts. Unset keeps the in-memory store (dev, tests).
	var store Store = NewMemoryStore()
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pg, err := NewPostgresStore(ctx, dsn)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		store = pg
		log.Print("ingest storage: postgres (exactly-once survives restarts)")
	} else {
		log.Print("ingest storage: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{store: store}
	log.Printf("OpenHealth OS sync-ingest on %s (idempotent, gzip JSON-lines)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
