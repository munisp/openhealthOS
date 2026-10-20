# ADR-0044: Postgres connection pooling — PgBouncer yes, Pgpool-II no

Status: accepted (2026-10-20)

## Context

Nine Go services on the platform plane each hold a `database/sql` pool
against one `postgres:16`. Postgres forks a backend (~10 MB) per
connection; ~90 mostly-idle backends is real memory on small planes,
and the connection ceiling arrives exactly when a sync burst hits —
the worst time to start refusing work.

Two candidates were evaluated.

## Decision

**PgBouncer: adopted.** One container in front of the platform Postgres
(`infra/compose/platform.yaml`, config in `infra/postgres/`):

- **Transaction pooling.** Server connections are held only for the
  duration of a transaction. Our services use stdlib `database/sql`
  with no session state (no `SET`, no advisory locks, no cross-query
  `LISTEN/NOTIFY`), so this mode is safe and collapses ~90 client
  connections to ~25 real backends.
- **Burst absorption.** `max_client_conn=1000` queues a sync burst in
  the pooler instead of forking Postgres into swap.
- **Sizing matches the tuning profile** (`deploy/tuning/postgres`):
  25 default backends + 5 reserve, idle hygiene for planes that go
  quiet overnight.
- **Migrations bypass the pooler** on the direct port — DDL and
  multi-statement scripts keep full session semantics.
- **Credentials are SCRAM verifiers** copied from `pg_authid`, never
  plaintext (the committed `userlist.txt` is a placeholder, replaced
  at deploy).
- **Observability:** `SHOW POOLS` / `SHOW STATS` via the admin console
  feed the ADR-0040 plane through the postgres-exporter scrape path.

**Pgpool-II: rejected, deliberately.** Its headline features are either
redundant or harmful here:

- Load balancing / read replicas — we run a single-writer doctrine per
  state plane (federal model, ADR-0002); there is no replica set to
  balance. When a state graduates to replicas, Kubernetes Services +
  PgBouncer target lists already cover read routing without a second
  query-aware proxy in the path.
- Automatic failover / watchdog — Kubernetes restart policies and
  (production) the operator pattern own HA; a second failover brain
  (Pgpool watchdog) is a split-brain risk, not a safety net.
- Query cache — unsafe for clinical data; staleness at the PHC layer
  is a patient-safety bug, not a performance feature.
- Connection pooling — its pooler is strictly weaker than PgBouncer's
  (no transaction-mode parity without the rest of the machinery).

Adopting Pgpool-II would add ~1 GB of footprint and a second consensus
protocol to operate in exchange for features we either already have or
explicitly do not want.

## Consequences

- One new container (64 MB limit), one new credential file to manage.
- Services must not add session-level Postgres features without
  revisiting this ADR — transaction pooling will break them loudly,
  which is the correct failure mode.
- Postgres `max_connections` can now be sized for real backends (~30),
  not for the sum of every client pool.
