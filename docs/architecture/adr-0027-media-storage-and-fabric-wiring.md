# ADR-0027: Media blob storage and Phase-2 fabric wiring

Status: accepted
Date: 2026-10-19

## Context

ADR-0026 shipped telemedicine with two recorded limits: consult photos were
`blob://` references with no store behind them, and LiveKit join tokens were
minted but nothing deployed the fabric they connect to. Both are now closed.

## Decision

**`services/media-service`** (Go, stdlib, :8095) — the bytes behind every
`blob://media/{id}` pointer:

- **Two backends, one contract.** `LocalStore` (filesystem) is the default —
  offline-first, right for facility servers and pilots. `S3Store` targets any
  S3-compatible object store (MinIO-class, AGPLv3 — composed over the S3 wire
  protocol per doctrine, never embedded) for state/national scale, with
  hand-rolled SigV4 signing in stdlib (the AWS SDK would dwarf the service).
- **Tamper-evident.** SHA-256 on upload, verified on every read; a corrupted
  or tampered object is never served. Same doctrine as the audit lake.
- **Evidence, not clutter.** Uploads name an actor; deletes are soft
  tombstones with who + why; metadata lives in per-blob JSON sidecars so a
  restart rebuilds the index with the audit trail intact (both backends).
- **25 MiB cap** — clinical photos and short videos, not archives.

**Phase-2 wiring.** The `deploy/voice/phase2-livekit` compose now includes
`telemed-service` (signed against the same `LK_API_KEY`/`LK_API_SECRET` as
the LiveKit fabric it fronts) and `media-service`, so consult escalation
produces rooms that genuinely connect on the deployed fabric. Media's
standalone compose (`deploy/media/`) documents the MinIO flip
(`S3_BACKEND=s3`) without changing the `blob://` contract.

## Consequences

- `demo/telemed.sh` now proves the full loop: real photo upload → reference
  in a consult → integrity-verified download → escalation → verified tokens.
- Remaining honest note: MinIO itself is not run in CI (no Docker here); the
  S3 backend is tested against an in-memory S3-compatible fake that asserts
  every request is SigV4-signed. First real MinIO deployment should run one
  manual upload/download before go-live.
