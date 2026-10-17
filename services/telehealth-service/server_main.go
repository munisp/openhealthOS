package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	addr := ":8087"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	s := server{svc: NewService()}
	log.Printf("OpenHealth OS telehealth-service on %s (nurse-first consults + referrals)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
