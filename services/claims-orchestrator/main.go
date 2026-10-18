package main

import (
	"encoding/json"
	"github.com/munisp/openhealthOS/shared/servex"
	"log"
	"net/http"
	"os"

	"github.com/munisp/openhealthOS/shared/authz"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	adj := SubprocessAdjudicator{
		Python: env("PYTHON", "python3"),
		Script: env("ADJUDICATOR", "../../packages/adjudicator/adjudicate.py"),
	}
	tally := NewPayerTally()
	h := claimsHandler{
		Adjudicator: adj,
		Ledger:      SubprocessLedger{Binary: env("LEDGER", "../ledger/target/release/oh_ledger")},
		Switch:      HTTPSwitch{BaseURL: env("SWITCH", "http://127.0.0.1:9090")},
		Tally:       tally,
	}
	mux := http.NewServeMux()
	mux.Handle("/claims", h)
	mux.HandleFunc("/payers/stats", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "GET only", http.StatusMethodNotAllowed)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"payers": tally.Stats()})
	})
	mux.Handle("/eligibility", eligibilityHandler{Checker: adj})
	mux.Handle("/enrolments", enrolmentHandler{Enroller: adj})
	mux.Handle("/preauth", preauthHandler{Authorizer: adj})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	addr := env("LISTEN", ":8080")
	log.Printf("OpenHealth OS claims orchestrator on %s (switch=%s)", addr, env("SWITCH", "http://127.0.0.1:9090"))
	handler := http.Handler(mux)
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		log.Fatal(authErr)
	}
	if authV != nil {
		handler = authV.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}
