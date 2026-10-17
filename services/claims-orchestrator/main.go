package main

import (
	"log"
	"net/http"
	"os"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func main() {
	h := claimsHandler{
		Adjudicator: SubprocessAdjudicator{
			Python: env("PYTHON", "python3"),
			Script: env("ADJUDICATOR", "../../packages/adjudicator/adjudicate.py"),
		},
		Ledger: SubprocessLedger{Binary: env("LEDGER", "../ledger/target/release/oh_ledger")},
		Switch: HTTPSwitch{BaseURL: env("SWITCH", "http://127.0.0.1:9090")},
	}
	mux := http.NewServeMux()
	mux.Handle("/claims", h)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	addr := env("LISTEN", ":8080")
	log.Printf("OpenHealth OS claims orchestrator on %s (switch=%s)", addr, env("SWITCH", "http://127.0.0.1:9090"))
	log.Fatal(http.ListenAndServe(addr, mux))
}
