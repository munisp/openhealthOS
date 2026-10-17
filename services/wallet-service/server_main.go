package main

import (
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
	log.Fatal(http.ListenAndServe(addr, s.routes()))
}
