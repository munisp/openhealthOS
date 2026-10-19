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
	s := server{store: store, ix: newIntegrations(os.Getenv), fleet: NewFleetStore(), mci: NewMCIStore(), voice: newVoiceDialogues()}
	log.Printf("dispatch-service listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, cors(s.routes())))
}

// cors lets the patient PWA and ambulance map — served from a different
// origin — reach the public emergency endpoints. No cookies or auth headers
// are used on this surface, so a wildcard origin carries no ambient
// credential risk; operator endpoints sit behind the gateway regardless.
func cors(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		h.ServeHTTP(w, r)
	})
}
