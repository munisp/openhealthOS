# Sync agent (Go) — the offline-first seam

Store-and-forward sync for the facility edge (ADR-0001):

- The local data plane writes one JSON file per domain event into the outbox.
- The agent packs oldest-first events into **gzip batches capped at 1 MiB** —
  the cap is enforced on the compressed payload, i.e. what actually goes on
  the wire (NFR: <1MB/facility/day).
- Batches POST to the state plane ingest endpoint. **The outbox is only
  cleared after a 2xx ACK** — at-least-once delivery, no silent loss; the
  ingest side is idempotent on event id.
- Any failure (offline, ingest down) leaves the outbox untouched and the
  facility keeps operating fully offline.

## Run

```bash
OUTBOX=./facility.outbox INGEST_URL=https://state-plane.example/ingest go run .
```

## Test

```bash
go test ./...
```

Tests cover: compressed-cap enforcement, outbox retention on failure, full
drain on success, and loud failure on an oversized single event.
