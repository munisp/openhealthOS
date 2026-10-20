# APISIX — third-party integration gateway (ADR-0062)

Apache APISIX (Apache-2.0, composed not forked) fronts all north-south
third-party traffic: payers, insurers, labs, pharmacies, vendors, analytics.

## Why standalone mode

`apisix.yaml` is declarative and file-reloaded — no etcd control plane, so
the gateway survives exactly like the rest of the stack (offline doctrine).
GitOps owns this file; a change is a PR, not a dashboard click.

## Run (state plane)

```sh
docker compose -f infra/compose/platform.yaml up apisix
```

Env required: `KEYCLOAK_DISCOVERY` (realm .well-known URL),
`APISIX_OIDC_SECRET`, one `CONSUMER_KEY_*` per onboarded integrator.

## What integrators see

- `401` — missing/expired token (SMART Backend Services via Keycloak)
- `403` — token valid, scope insufficient (defence in depth with upstream)
- `429` — per-consumer rate limit, `Retry-After`-style semantics
- `X-Request-Id` echoed on every response — quote it in support tickets

## Boundaries (honest)

- Facility-edge traffic (PWA sync, USSD, WhatsApp) stays on Caddy + the Go
  api-gateway (ADR-0042). APISIX does NOT replace them.
- HMAC webhook signing for outbound events stays in notification-service;
  APISIX is inbound-only.
- Scope sets are enforced at two layers: Keycloak client profile (issue
  time) and upstream service checks (request time). APISIX openid-connect
  validates the token; fine-grained FHIR scope checks stay in services.
