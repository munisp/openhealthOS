package main

import (
	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"
	"log"
	"net/http"
	"os"
)

func main() {
	addr := ":8084"
	if v := os.Getenv("LISTEN"); v != "" {
		addr = v
	}
	bin := os.Getenv("LEDGER")
	if bin == "" {
		bin = "../ledger/target/release/oh_ledger"
	}
	s := server{w: &WalletService{Ledger: SubprocessLedger{Binary: bin}}}
	log.Printf("OpenHealth OS wallet-service on %s (ledger=%s)", addr, bin)
	handler := http.Handler(s.routes())
	if v := authz.FromEnv(os.Getenv); v != nil {
		handler = v.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.ListenAndServe(addr, handler, servex.Options{}))
}
