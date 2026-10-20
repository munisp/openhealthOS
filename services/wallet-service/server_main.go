package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/munisp/openhealthOS/shared/authz"
	"github.com/munisp/openhealthOS/shared/servex"
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
	wallet := &WalletService{Ledger: SubprocessLedger{Binary: bin}}
	// Idempotency records: Postgres on the state plane, memory at the edge.
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		pool, err := pgxpool.New(ctx, dsn)
		cancel()
		if err != nil {
			log.Fatalf("postgres: %v", err)
		}
		wallet.refs = NewPostgresIdemStore(pool)
		log.Print("wallet idempotency: postgres (state plane)")
	} else {
		wallet.refs = NewMemoryIdemStore()
		log.Print("wallet idempotency: in-memory (set DATABASE_URL for postgres)")
	}
	s := server{w: wallet}
	log.Printf("OpenHealth OS wallet-service on %s (ledger=%s)", addr, bin)
	handler := http.Handler(s.routes())
	authV, authErr := authz.RequireFromEnv(os.Getenv)
	if authErr != nil {
		log.Fatal(authErr)
	}
	if authV != nil {
		handler = authV.Middleware(handler)
		log.Print("oidc: enforcing bearer auth")
	}
	log.Fatal(servex.InstrumentedListenAndServe("wallet-service", os.Getenv, addr, handler))
}
