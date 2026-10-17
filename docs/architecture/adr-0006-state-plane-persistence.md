# ADR-0006: State-plane persistence — Postgres behind the seams

**Status**: accepted · **Date**: 2026-10-18

## Context

The platform's services were built storage-agnostic: every persistence
need sits behind a Go interface (`Store`, `PatientStore`, `backend`,
`ConsentService`) with an in-memory implementation serving edge nodes,
development, and tests. That got the runtime plane working, but the state
plane needs durability: a restarted sync-ingest must not lose its dedup
set (exactly-once depends on it), a restarted consent service must not
resurrect revoked artefacts, and the SHR access audit must survive
process death — a denied read that leaves no trace is a breach.

## Decision

**Postgres 16 on the state plane, bound behind the existing seams via
`DATABASE_URL`.** Schema lives in `db/migrations/` (idempotent, replayed
at boot). Services select storage at startup: `DATABASE_URL` set →
Postgres; unset → in-memory. Nothing else changes — the seam contract
tests run against both implementations, and CI runs the Postgres variants
against a service container (`TEST_DATABASE_URL`).

Per-store durability choices:

- **sync-ingest**: the primary key on `sync_events.id` *is* the dedup
  set — `INSERT … ON CONFLICT DO NOTHING`, so replayed batches after a
  crash report as duplicates, never as new data.
- **consent-service**: artefacts carry the same HMAC-SHA256 signature as
  the in-memory manager (shared `signWith`), so an artefact issued before
  a restart verifies after it, and tampering is still caught on every
  check. Artefact ids come from a durable sequence.
- **shr-service**: the access rule never moved — it lives in
  `Store.authorizeLocked` and runs identically over either backend;
  backends store and fetch, never decide. The audit log is append-only.
- **registry-service**: offline patient ids keep their edge semantics —
  per-facility sequences (`facility_seq`) increment atomically, so ids
  stay unique and locally resolvable with zero coordination.

## Consequences

- pgx v5 (MIT) becomes the only third-party Go dependency; recorded in
  the LICENSING.md matrix and pinned in `go.mod`.
- The money path is unchanged and deliberately untouched: the ledger is
  the Rust double-entry service; no balances live in Postgres.
- OIDC (`shared/authz`) composes orthogonally: authentication at the
  gateway, attribution in the audit log, authorization in the store.
