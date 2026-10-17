package main

import (
	"log"
	"net/http"
	"os"
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
	s := server{m: NewManager(key)}
	log.Printf("OpenHealth OS consent-service on %s (HIE-CM pattern)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
