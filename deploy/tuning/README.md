# Platform tuning & throughput doctrine

"Millions of transactions per second" is a property of the **hot path**, not
of every service. The platform rule:

- **Go** — service hot paths: gateways, ingest, notification rail. Stdlib
  only, bounded queues, backpressure over blocking. Measured:
  sync-ingest clears ~200k events/sec in a single process on 2 cores
  (`go test -bench=. ./services/sync-ingest`) — a small replica set plus the
  Kafka/Fluvio fan-out rolls up to platform-scale millions.
- **Rust** — latency-critical engines: ledger (services/ledger), telemetry
  hot path. Measured: telemetry-collector clears >1.7M readings/sec after
  the zero-allocation rewrite (`cargo test --release hot_path`).
- **Python** — deliberately **off** the hot path: orchestration, analytics,
  ML tooling. Never in a request loop that patients or payments wait on.

Every profile in this tree names its observability hook — tuning without
measurement is superstition (ADR-0040).

| Component | Profile | The one knob that matters most |
|---|---|---|
| postgres | `postgres/postgresql.conf`, `postgres/pgbouncer.ini` | pgbouncer transaction pooling — connections are the ceiling |
| kafka | `kafka/server.properties` | producer batching (linger 5ms, zstd) |
| redis | `redis/redis.conf` | `maxmemory` + allkeys-lru — never OOM-kill |
| opensearch | `opensearch/jvm.options.d-tuning` | heap = ½ RAM, refresh_interval 30s on the audit lake |
| apisix | `apisix/config.yaml.snippet` | worker_connections + native opentelemetry plugin |
| dapr | `dapr/configuration.yaml` | OTLP tracing on, sampling deliberate |
| temporal | `temporal/dynamicconfig.yaml` | history shards before frontend replicas |
| permify | `permify/config.yaml.snippet` | schema cache TTL + native OTLP tracer/meter |
| fluvio | `fluvio/profile.md` | partition hot topics by tenant key |
| mojaloop | `mojaloop/profile.md` | current releases are Postgres-native; TigerBeetle is the roadmap |
| tigerbeetle | `tigerbeetle/profile.md` | batch up to 8189 ops per request, pipelined |
| lakehouse | `lakehouse/profile.md` | compact small files nightly |

Non-HTTP components (TigerBeetle, batch jobs) emit stats JSONL that
[services/stats-bridge](../../services/stats-bridge) renders as Prometheus
`/metrics` for the collector.
