# Wallet service (Go) — payments module

Facility wallets and **premium micro-collections** (₦500–2,000 instalments —
93% of employment is informal, so premiums arrive in small, irregular mobile
payments). Every movement is a double-entry posting on the
[ledger seam](../ledger) (ADR-0003): balances are never hand-rolled here,
and overdrafts are refused by construction.

- `POST /wallets` — open a facility wallet
- `POST /premiums` — book a member instalment into the scheme pool
- `POST /payouts` — pay a facility wallet from the pool (claim settlement)

```bash
LEDGER=../ledger/target/release/oh_ledger go run . && go test ./...
```
