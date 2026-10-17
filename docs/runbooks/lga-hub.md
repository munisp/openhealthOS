# Runbook: LGA hub operations

The LGA hub (`services/lga-hub`) is the store-and-forward tier between
facility edges and the state plane (ADR-0011). One hub per LGA. It is
deliberately simple; this runbook covers the failure modes that matter.

## Deploy

```sh
lga-hub   # binary, stdlib-only + shared modules
```

| Env | Default | Purpose |
| --- | --- | --- |
| `LISTEN` | `:8090` | HTTP listen address |
| `SPILL_PATH` | _(unset = memory)_ | JSONL spill file — **set this in production** |
| `UPSTREAM_URL` | _(unset = buffer only)_ | state-plane sync-ingest base URL |
| `FORWARD_INTERVAL_SECONDS` | `15` | forwarding tick |
| `HUB_ID` | hostname-derived | appears in ingest acks and stats |

The spill file must live on local disk that survives reboots (SD/eMMC on
an edge box is fine). Two hubs must never share one `SPILL_PATH`.

## Health and observability

- `GET /healthz` — liveness.
- `GET /stats` — the operational dashboard:
  - `pending.critical / standard / bulk` — queue depth per class.
  - `forwarded_total` — lifetime forwards.
  - `last_upstream_ok`, `last_upstream_error` — uplink health.

Alert when `pending.critical > 0` for more than two forwarding ticks, or
when `last_upstream_ok` is stale while queues grow.

## Failure modes

**State plane unreachable.** Expected and safe. The hub stops the pass,
stamps `last_upstream_error`, and keeps buffering to the spill. No
operator action; forwarding resumes on the next tick after the uplink
returns. Watch disk if the outage is long (JSONL is ~1–2 KB/event; a
million queued events ≈ 1–2 GB).

**Power cut / process kill.** Safe by design. Every event and every
forwarded-mark is fsynced before acknowledgment. On restart the hub
replays the spill (data lines + ack lines) and resumes; already-
forwarded events do not resurrect. Verified by
`demo/national-sync.sh` step 4.

**Spill file corrupt at the tail** (torn write on power loss). Replay
tolerates a truncated final line — the event is re-ingested by the
facility's sync agent retry, and idempotent ingest dedupes it. Do not
hand-edit the spill file; if it must be moved, stop the hub first.

**Duplicate floods from a retrying facility.** Harmless. Duplicate IDs
are acknowledged (`duplicates: n`) without re-storage.

**Compaction.** Automatic past 10 000 retired entries: the file is
rewritten with unforwarded events only. Brief pause in ingest latency;
no action needed.

## What the hub never does

- Never transforms payloads (the envelope is end-to-end).
- Never drops an event because upstream is down.
- Never re-orders critical behind bulk.
- Never requires the state plane schema — it forwards NDJSON to
  `/ingest/raw` and nothing else.

If you are tempted to add logic to the hub, put it in the state plane
instead: 774 hubs must stay identical, dumb, and replaceable.
