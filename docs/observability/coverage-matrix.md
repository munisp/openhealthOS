# Middleware observability coverage matrix (ADR-0040)

How every middleware component feeds the OpenTelemetry plane
(`infra/observability/otel-collector-config.yaml`). Three mechanisms, in
order of preference:

1. **Native OTLP** — the component speaks OpenTelemetry itself → push to the collector.
2. **Prometheus scrape** — the component exposes `/metrics` → collector `prometheus` receiver.
3. **Boundary instrumentation** — no usable native telemetry → our own
   Go/Rust/Python code that *calls* the component is instrumented with
   shared/otelx spans + metrics at the integration seam (honest coverage:
   we see every interaction, latency and error, from the platform side).

Tenant slicing: platform services stamp `tenant.id` from the propagated
`x-tenant-id` header (shared/otelx `TenantMiddleware`); middleware-level
series (Kafka lag, Postgres connections) are platform-scoped and joined to
tenants via the service-level series in Grafana.

| Component | Mechanism | Config / integration | Per-tenant | Caveats |
|---|---|---|---|---|
| **OpenHealth services** (api-gateway, notification-service, whatsapp-gateway, all Go) | Native OTLP/HTTP + `/metrics` | `otelx.Instrument(service, env)`, `OTEL_EXPORTER_OTLP_ENDPOINT` | ✅ `tenant.id` on every span + metric series | stdlib-only implementation; swap for the OTel SDK behind the same two functions if ever needed |
| **Kafka** | Prometheus scrape via collector | `kafkametrics` receiver (brokers/topics/consumers) | platform-level | consumer-group lag is the alert that matters (`KafkaConsumerLag`) |
| **Dapr** | Native OTLP tracing (sidecar config `tracing` → OTLP endpoint) | Dapr `Configuration` CRD `spec.tracing.otlp.endpointAddress: otel-collector:4317` | app-scoped | metrics via sidecar `/metrics` scrape |
| **Fluvio** | Boundary instrumentation | produce/consume calls wrapped in otelx spans at our adapters; SPU metrics via Prometheus exporter endpoint when enabled | platform-level | no native OTel upstream (verified 2026-10); do not claim span-level coverage inside the SPU |
| **Temporal** | Native OTel interceptors + metrics handler | Go SDK `contrib/opentelemetry`: `NewTracingInterceptor`, `NewMetricsHandler`; server metrics scraped on :8000 | workflow-tag `tenant` | verified: go.temporal.io/sdk/contrib/opentelemetry |
| **Postgres** | Collector receiver | `postgresql` receiver (connections, tx rate, replication lag) | platform-level | per-tenant DB load inferred via service spans with `tenant.id` |
| **Keycloak** | Native metrics + OTel tracing | `--metrics-enabled=true --tracing-enabled=true` (KC 25+: `--tracing-endpoint=http://otel-collector:4317`) | realm = tenant | realm-level series give per-tenant auth health |
| **Permify** | Native OTLP tracer + meter | `tracer.exporter: otlp, endpoint: otel-collector:4317`; `meter.exporter: otlp` | platform-level | verified: native OTel config (tracer + meter blocks) |
| **Redis** | Collector receiver | `redis` receiver (memory, evictions, hit ratio) | platform-level | eviction alert guards the JWKS warm standby |
| **Mojaloop** | Prometheus scrape | hapi-prometheus `/metrics` on central-ledger, account-lookup, quoting | platform-level | ledger DB is Postgres in current releases (see deploy/tuning/mojaloop) |
| **OpenSearch** | Prometheus scrape | `prometheus-exporter` plugin endpoint `_plugins/prometheus/metrics` | platform-level | cluster-status-red alert wired |
| **openappsec** | Boundary + logs | agent decision logs tailed via collector `filelog`; our edge WAF (shared/wafx) emits reject spans | platform-level | no OTel upstream (verified 2026-10); coverage is at the platform boundary |
| **APISIX** | Native OTel plugin + Prometheus plugin | `plugins: [opentelemetry, prometheus]`; `plugin_attr.opentelemetry.collector.address: otel-collector:4318` | route-level | verified: native opentelemetry plugin (binary OTLP over HTTP) |
| **TigerBeetle** | Sidecar exporter | stats socket → `deploy/tuning/tigerbeetle/exporter` → Prometheus | platform-level | no HTTP endpoint by design; the sidecar is our code |
| **Apache Sedona** | Boundary instrumentation | geospatial jobs are invoked from Python/Go — spans around query/submit, metrics for rows/duration | ✅ `tenant.id` on the calling span | in-process library, not a server — boundary is the only truthful coverage |
| **GeoLibre** | Boundary instrumentation | same pattern: our geo services wrap calls in otelx spans | ✅ on the calling span | tracked as an evaluation dependency, not embedded (licence doctrine) |
| **Lakehouse** (packages/lakehouse) | Platform SDK (ours) | our Go/Python pipeline code emits spans + metrics per job; object-storage/warehouse metrics via their exporters | ✅ job-level `tenant` label | the lakehouse is our code on top of open formats — full coverage |
| **howUdeyChat** (admin, console, gateway bots) | otelx (Go services) / client counters (JS) | same `otelx.Instrument` pattern in Go; the phone app reports battery/memory counters via a `POST /telemetry` intake | ✅ per tenant | phone-side telemetry is opt-in and clinical-detail-free |

## What "covered" means — honestly

- **Full span-level**: OpenHealth services, APISIX, Temporal, Permify, Dapr, Keycloak (traces + metrics + logs).
- **Metrics-level, platform-scoped**: Kafka, Postgres, Redis, OpenSearch, Mojaloop, TigerBeetle.
- **Boundary-level**: Fluvio, openappsec, Sedona, GeoLibre — every call from
  the platform is traced and metered; internals are not. If upstream ships
  native OTel later, the collector config gains a receiver and nothing else
  changes.

## Alert path

Prometheus evaluates `infra/observability/rules/platform-alerts.yml` →
Alertmanager routes on the `tenant` label → `POST /alerts/intake` on
notification-service → ops contacts via the platform's own provider chain
(Matrix first, SMS fallback). Open source end to end; no SaaS dependency in
the alert path.
