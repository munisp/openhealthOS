package main

import (
	"log"
	"net/http"
	"os"
)

func main() {
	addr := ":8081"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	s := server{store: NewMemoryStore()}
	mux := s.routes()
	registerPatientRoutes(mux, NewMemoryPatientStore())
	log.Printf("OpenHealth OS registry service on %s (facilities, providers, patients)", addr)
	log.Fatal(http.ListenAndServe(addr, mux))
}
