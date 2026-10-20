# Chaos runbook (ADR-0059)

Doctrine: we prove fail-soft claims by killing things. Every scenario must end
with the platform DEGRADED-BUT-HONEST (UI shows staleness, queues hold, no
silent loss), never down-hard unless security demands fail-closed.

| # | Kill | Expected behaviour | Verify with |
|---|------|--------------------|-------------|
| 1 | Postgres (primary) | Writes queue at edge; reads serve last-known with staleness banner | dashboards show "last synced"; sync-agent queue depth grows |
| 2 | Caddy edge | Facility PWAs keep working offline (CRDT local-first); USSD unaffected (separate rail) | PWA task flow end-to-end offline |
| 3 | NIMC/NIN upstream | NIN adapter returns `unverified`/`verified-stale`; enrolment continues | `packages/nin` states |
| 4 | SHR | CDS immig cards go empty + warning; IPS/SHL mint returns 503 honest error | cds-service fail-soft test |
| 5 | Bulk export mid-run | Partial manifest with warnings; no empty files advertised | bulk-export-service tests |
| 6 | Network partition 30min, then heal | Sync-storm: N facilities flush queues, no event loss, no duplicate application | `benchmark/sync_storm.py` + idempotency keys |
| 7 | Keycloak down | Step-up re-auth defers; existing sessions continue to expiry; new logins queued message | ADR-0039 behaviour |

Run scenario 6 at least quarterly at 100-facility scale before any national
rollout gate. Record results in docs/observability/chaos-log.md.
