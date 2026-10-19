# ADR-0040: Observability plane — OpenTelemetry everywhere, per-tenant

**Status:** accepted (2026-10-20)

## Context

A national health platform is unobservable by default: dozens of services,
a middleware zoo (Kafka, Dapr, Fluvio, Temporal, Postgres, Keycloak,
Permify, Redis, Mojaloop, OpenSearch, openappsec, APISIX, TigerBeetle,
Sedona, GeoLibre, the lakehouse), and multiple sovereign tenants whose
outages must never average out in a shared dashboard. The alert path must
also be sovereign: no SaaS paging dependency in a developing-country
deployment.

## Decision

**One telemetry plane, open source end to end.** Every signal flows through
an OpenTelemetry Collector (`infra/observability/otel-collector-config.yaml`)
into Prometheus (metrics + alert evaluation), Alertmanager (routing), and
Grafana (dashboards); traces land in the JSONL spans lake.

1. **Platform services**: `shared/otelx` — stdlib-only W3C traceparent
   propagation, OTLP/HTTP span export with bounded drop-oldest batching
   (telemetry sheds load, clinical traffic never blocks), Prometheus
   text-format `/metrics`, and RED middleware. `otelx.Instrument(service,
   env)` is the one-call wiring; api-gateway, notification-service and
   whatsapp-gateway are instrumented, and the pattern is two lines per
   remaining service.
2. **Per-tenant slicing**: the tenant is resolved at the edge and propagated
   as the `x-tenant-id` header beside `traceparent`; every span and metric
   series carries `tenant.id`. Alertmanager routes on the tenant label;
   per-tenant alert rules (`TenantErrorRateHigh`, `TenantSilent`) fire
   independently of the platform average.
3. **Middleware coverage** by mechanism, verified per component in
   [docs/observability/coverage-matrix.md](../observability/coverage-matrix.md):
   native OTLP (APISIX, Temporal, Permify, Keycloak, Dapr), collector
   receivers (Postgres, Redis, Kafka), Prometheus scrape (Mojaloop,
   OpenSearch, TigerBeetle via our stats-bridge sidecar), boundary
   instrumentation where upstream has no OTel (Fluvio, openappsec, Sedona,
   GeoLibre) — honest coverage at the integration seam, no false claims.
4. **The alert rail is the notification rail.** Alertmanager webhooks into
   notification-service `POST /alerts/intake`, which fans alerts out to
   per-tenant ops contacts through the platform's own provider chain —
   Matrix first, SMS fallback. Fail-loud: an alert with no configured
   contact returns 503 so Alertmanager retries; an alert about broken
   notifications can still exit through a healthy leg.

## Consequences

- Deploy: `docker compose -f infra/compose/platform.yaml -f
  infra/compose/observability.yaml up` — the whole plane is open source
  (OTel Collector, Prometheus, Alertmanager, Grafana; all Apache-2.0).
- otelx stays stdlib-only per the licence doctrine; the OTLP wire format is
  an open specification, and a deployment may swap otelx for the OTel SDK
  behind the same two functions without touching service code.
- Every tuning profile in `deploy/tuning/` names its observability hook, so
  performance work is measured, not asserted.
