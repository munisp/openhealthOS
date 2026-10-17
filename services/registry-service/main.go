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
	log.Printf("OpenHealth OS registry service on %s", addr)
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
