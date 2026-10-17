// OpenHealth OS — mock Mojaloop-pattern switch (development fixture).
//
// Implements FSPIOP-shaped POST /quotes and POST /transfers with an
// ILP-style packet stub, so the claims pipeline can be developed and
// conformance-tested without a real switch deployment.
//
// WARNING: test fixture only. Never deploy as a settlement endpoint.
// Production binds the same seams to a real Mojaloop-pattern switch
// (hub + DFSP principals), which differs per state scheme deployment.
package main

import (
	"encoding/json"
	"fmt"
	"github.com/munisp/openhealthOS/shared/servex"
	"log"
	"net/http"
	"sync/atomic"
	"time"
)

var qseq, tseq atomic.Int64

func quotes(w http.ResponseWriter, r *http.Request) {
	var m map[string]any
	if err := json.NewDecoder(r.Body).Decode(&m); err != nil {
		http.Error(w, "bad quote", http.StatusBadRequest)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"quoteId":    fmt.Sprintf("QUOTE-%d", qseq.Add(1)),
		"condition":  "mock-condition",
		"expiration": m["expiration"],
	})
}

func transfers(w http.ResponseWriter, r *http.Request) {
	var m map[string]any
	if err := json.NewDecoder(r.Body).Decode(&m); err != nil || m["ilpPacket"] == nil {
		http.Error(w, "bad transfer", http.StatusBadRequest)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{
		"transferId":  fmt.Sprintf("TRANSFER-%d", tseq.Add(1)),
		"status":      "COMPLETED",
		"completedAt": time.Now().UTC().Format(time.RFC3339),
	})
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/quotes", quotes)
	mux.HandleFunc("/transfers", transfers)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.Write([]byte("ok")) })
	log.Println("mock Mojaloop-pattern switch on :9090 (TEST FIXTURE — not a settlement endpoint)")
	log.Fatal(servex.ListenAndServe(":9090", mux, servex.Options{}))
}
