# ADR-0062: Apache APISIX as the third-party integration gateway

Date: 2026-10-20 · Status: accepted

## Context

ADR-0061 defined the integration surface; this decides the enforcement
point. The hand-rolled Go api-gateway is right for facility-edge routing
(small, stdlib, ours), but third-party north-south traffic needs a
battle-tested gateway feature set: per-consumer rate limiting, OIDC token
validation, request ids, Prometheus metrics, mTLS, and a declarative config
that GitOps can own.

## Decision

**Apache APISIX (Apache-2.0 — composed, never forked, per ADR-0005) fronts
all third-party traffic**, in standalone mode (`infra/apisix/apisix.yaml` —
declarative, file-reloaded, no etcd; offline doctrine holds for the gateway
itself).

- **AuthN**: openid-connect plugin, bearer-only, against the Keycloak realm
  (SMART Backend Services tokens from ADR-0061 onboarding).
- **Rate limits**: limit-count per consumer — 600/min claims+terminology,
  300/min FHIR+CDS, 10/min bulk $export; 429s are honest and observable.
- **Observability**: prometheus plugin + request-id echoed on responses.
- **Defence in depth**: token validation at APISIX, scope/tenant checks stay
  in upstream services. Consumer secrets are env-injected, never committed
  (test-gated).
- **Route table is test-locked** (`infra/apisix/test_apisix_config.py`):
  every SDK path must have an authenticated route; no route ships without
  authn; no secrets in the file.

## Boundaries (explicit)

- Facility-edge traffic (PWA sync, USSD, WhatsApp, QR) stays on Caddy + the
  Go api-gateway (ADR-0042/0053). APISIX is north-south only.
- Outbound webhooks keep HMAC signing in notification-service; APISIX is
  inbound.
- A catch-all `/api/*` route to the Go gateway preserves backwards
  compatibility during migration and is scheduled for removal.

## Consequences

- Positive: integrators get a standard, documented gateway behaviour set;
  operators get metrics and rate control without writing Go; config changes
  are PRs.
- Limitations (honest): APISIX adds an OpenResty/Lua runtime to operate
  (contained: one container, no etcd); scope enforcement remains two-layer
  by design; the catch-all route's removal is a tracked migration task.
