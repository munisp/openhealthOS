package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	addr := ":8443"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	s := server{store: NewMemoryStore()}
	log.Printf("OpenHealth OS sync-ingest on %s (idempotent, gzip JSON-lines)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
