# ADR-0003: Money on a double-entry ledger, never hand-rolled balances

- Status: Accepted
- Date: 2026-10-17
- Spec reference: §5.8 (TigerBeetle), §4.3.4–4.3.5

## Context

Claims and payments are the product. Nigerian payment cycles of 6–24 months on paper are normal; leakage is the pitch we attack. Any money movement that is not a balanced, auditable double-entry posting is an eventual fraud or reconciliation incident. Balances must never be hand-rolled tables.

## Decision

1. **Double-entry ledger as the system of record** for facility wallets, premium accounts and provider payable balances. Amounts are integer minor units (kobo), never floats.
2. **Production engine: TigerBeetle** (Apache-2.0, purpose-built financial ledger). The repo ships `services/ledger`, a TigerBeetle-inspired seam implementing the same primitives — i128 minor units, two-phase commit (`prepare`/`commit`/`abort`), no negative balances, double-open rejection — so the pipeline runs and is tested without the production dependency. The seam is the contract; TigerBeetle binds behind it.
3. **Settlement follows Mojaloop patterns**: FSPIOP-shaped `/quotes` then `/transfers` with an ILP-style packet. `services/payment-switch-mock` implements the shape for development; production binds to a real switch deployment per state scheme.
4. **Every adjudication and posting is an event** on the Kafka backbone — durable, replayable, auditable by design. Temporal durable workflows orchestrate multi-day adjudication and payment runs so any past decision can be re-derived from the same events.
5. Claims and eligibility **degrade gracefully to offline queue** (ADR-0001); the ledger is authoritative once connectivity returns.

## Consequences

- Zero balance drift is a testable invariant (`cargo test` enforces overdraft refusal, 2PC semantics, idempotent double-open rejection).
- Fraud and audit become queries over the event log, not forensic projects.
- Micro-collections (₦500–2,000 premium instalments) are first-class ledger flows, not edge cases.
