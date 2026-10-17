# Claims orchestrator (Go)

The money path, end to end:

```
claim (JSON) ─► adjudicator (packages/adjudicator — eligibility, benefit, tariff, fraud flags)
              ─► ledger (services/ledger — double-entry posting, minor units)
              ─► settlement switch (Mojaloop-pattern FSPIOP /quotes + /transfers)
```

## Design

- **Every dependency is an interface** (`seams.go`): `Adjudicator`, `Ledger`, `Switch`.
  Tests run the full handler against fakes — the pipeline is conformance-tested
  without Python, Rust, or network. Production wiring uses the subprocess and
  HTTP implementations.
- **Zero unaccounted outcomes**: only `APPROVE` with a positive payable amount
  touches the ledger or the switch. `PEND` queues for human review; `DENY` is a
  terminal, auditable state. Ledger/settlement failures are surfaced in the
  response, never swallowed.
- Clean-room re-implementation of the adjudication pipeline pattern
  (see ADR-0005 — BSL-licensed reference designs are never embedded).

## Run

```bash
go build ./... && LISTEN=:8080 SWITCH=http://127.0.0.1:9090 ./claims-orchestrator
```

Environment: `LISTEN`, `SWITCH`, `PYTHON`, `ADJUDICATOR`, `LEDGER`.

## Test

```bash
go test ./...
```
