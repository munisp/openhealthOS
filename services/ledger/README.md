# Ledger (Rust) — the money seam

Double-entry settlement ledger implementing TigerBeetle-inspired primitives
(ADR-0003): integer minor units (i128 kobo, never floats), two-phase commit
(`prepare`/`commit`/`abort`), no negative balances, double-open rejection.

**Production intent**: TigerBeetle itself binds behind this same interface —
the seam is the contract, so the claims pipeline runs and is conformance-tested
without the production dependency.

## Interface (JSON lines on stdin/stdout)

```
{"op":"open","id":"WALLET-1","opening":0}
{"op":"post","debit":"SETTLE-POOL","credit":"PROVIDER-FLOAT","amount":1500,"tx":"C-1001"}
{"op":"prepare","txid":"TX-9","debit":"SETTLE-POOL","credit":"PROVIDER-FLOAT","amount":500}
{"op":"commit","txid":"TX-9"}
{"op":"balance","id":"PROVIDER-FLOAT"}
```

Boot chart of accounts: `SETTLE-POOL`, `PROVIDER-FLOAT`, `PLATFORM-FEE`.

## Invariants enforced by tests

- Overdraft refusal (balances never go negative — state never mutates on error)
- Non-positive amounts and self-transfers rejected
- 2PC semantics: prepared funds are held, not posted; abort releases
- Double-open rejected; negative opening rejected
- **Conservation of minor units** — total supply never changes across postings
  (the zero-drift guarantee)

```bash
cargo test
```
