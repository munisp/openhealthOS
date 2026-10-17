package main

import (
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
	h := claimsHandler{
		Adjudicator: adj,
		Ledger:      SubprocessLedger{Binary: env("LEDGER", "../ledger/target/release/oh_ledger")},
		Switch:      HTTPSwitch{BaseURL: env("SWITCH", "http://127.0.0.1:9090")},
	}
	mux := http.NewServeMux()
	mux.Handle("/claims", h)
	mux.Handle("/eligibility", eligibilityHandler{Checker: adj})
	mux.Handle("/preauth", preauthHandler{Authorizer: adj})
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	addr := env("LISTEN", ":8080")
	log.Printf("OpenHealth OS claims orchestrator on %s (switch=%s)", addr, env("SWITCH", "http://127.0.0.1:9090"))
	handler := http.Handler(mux)
	if v := authz.FromEnv(os.Getenv); v != nil {
		handler = v.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}
