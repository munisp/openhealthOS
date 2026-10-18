package main

import (
	"encoding/json"
	"log"
	"net/http"
	"os"
	"path/filepath"

	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

// resolveToolPath makes the default adjudicator path robust to the
// process cwd: as-given first, then relative to the executable (a
// service binary run from the repo root or a deploy dir finds the
// sibling packages/ tree the same way).
func resolveToolPath(p string) string {
	if _, err := os.Stat(p); err == nil {
		return p
	}
	if exe, err := os.Executable(); err == nil {
		cand := filepath.Join(filepath.Dir(exe), p)
		if _, err := os.Stat(cand); err == nil {
			return cand
		}
	}
	return p
}

func main() {
	adj := SubprocessAdjudicator{
		Python: env("PYTHON", "python3"),
		Script: resolveToolPath(env("ADJUDICATOR", "../../packages/adjudicator/adjudicate.py")),
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
