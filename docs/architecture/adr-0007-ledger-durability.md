# ADR-0007: Ledger durability — journal, snapshot, 2PC recovery

**Status**: accepted · **Date**: 2026-10-18

## Context

The double-entry ledger (ADR-0003) held accounts and prepared transfers
in process memory. A restart meant silent loss of balances — unacceptable
for the money path regardless of pilot scale. Production intent remains
TigerBeetle behind the same seam, but the reference implementation must
itself be safe to run.

## Decision

**Append-only journal + snapshots, stdlib-only, enabled by
`LEDGER_DATA_DIR`.** The journal is the op stream itself: every accepted
mutating op (`open/post/prepare/commit/abort`) is appended with flush +
fsync *after* successful application. Recovery replays the snapshot, then
the journal, through the same op handler — one code path, no divergence
between live and recovered state. A `snapshot` op (and an automatic
snapshot every 4096 ops) compacts state and truncates the journal.

Properties this buys:

- **2PC recovery is exact.** A crash between prepare and commit replays
  the transfer back into the prepared set; the orchestrator resolves it
  with commit/abort. Money is never duplicated and never vanishes mid-
  flight.
- **Torn writes are survived.** A trailing line without its closing brace
  (crash mid-append) is skipped on replay; everything before it is intact.
- **Boot chart ordering is explicit.** Chart accounts are seeded *before*
  replay, because journaled transfers may reference them. (The first
  version of this got it wrong; the cross-restart balance test catches it.)
- **Fail-closed boot.** Recovery failure exits non-zero — the process
  refuses to start with money at stake.

## Consequences

- fsync per accepted op bounds throughput; pilot volumes (thousands of
  claims/day/facility) are far inside that bound. If profiling ever says
  otherwise, the answer is TigerBeetle behind the seam — not weakening
  the durability guarantee.
- Compose binds `LEDGER_DATA_DIR` to named volumes for the claims
  orchestrator and wallet service. Subprocess env inheritance carries the
  setting from service to ledger unchanged.
- In-memory mode remains the default for tests and the demo.
