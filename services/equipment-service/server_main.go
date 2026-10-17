package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	addr := ":8086"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	s := server{svc: NewService()}
	log.Printf("OpenHealth OS equipment-service on %s (registry + BMET ticket routing)", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
