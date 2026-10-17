# Sync ingest (Go) — state-plane side of offline-first sync

Receives gzip batches of JSON-line domain events from edge
[`sync-agent`](../sync-agent)s. The edge guarantees at-least-once delivery;
this service guarantees **idempotency** — every event carries an id,
duplicates are acknowledged but applied exactly once. Together: no loss, no
double-apply.

- `POST /ingest` — gzip JSON-lines batch → ack `{received, applied, duplicates}`
- `GET /metrics` — received / applied / duplicates-dropped counters
- Events without an id are rejected: they cannot be safe under at-least-once.

```bash
LISTEN=:8443 go run . && go test ./...
```
