# ADR-0061: Third-party integration surface — API, SDK, onboarding

Date: 2026-10-20 · Status: accepted

## Context

Payers, insurers, laboratories, pharmacies, vendors and analytics consumers
need to integrate without bespoke per-party work. The surface must be
auditable (one door), safe (least privilege), and honest (fail-soft doctrine
extends to integrators).

## Decision

1. **One door**: all third-party traffic enters at the state-plane gateway;
   edges and the national core are never exposed.
2. **AuthN/Z**: SMART Backend Services (client_credentials) on the Keycloak
   realm, least-privilege scope sets per stakeholder class (payer / lab /
   pharmacy / analytics). Patient-context third parties use the PKCE public
   client (ADR-0054) — a separate flow.
3. **Surfaces**: claims gateway (eligibility/preauth/submit/status, OpenAPI
   contract in openapi/claims-gateway.yaml), FHIR resource APIs, Bulk
   $export (the ONLY analytics surface — no row-level query API for third
   parties), CDS Hooks bus, terminology validation.
4. **SDK**: `packages/ohsdk` (stdlib Python) — token caching, mandatory
   idempotency keys on mutations (409 = ConflictError, original landed),
   honest errors, no silent retries. OpenAPI specs generate clients for
   other languages.
5. **Onboarding**: registry-verified application -> scoped Keycloak client
   -> synthseed sandbox conformance gate -> production credentials
   (90-day secret rotation, optional mTLS for payer class).
6. **Push**: notification-service webhooks per tenant (HMAC-signed,
   at-least-once; consumers must be idempotent).

## Consequences

- Positive: a payer can integrate against the sandbox in a day; the same
  contract serves NHIA, state schemes and private HMOs; analytics consumers
  cannot exfiltrate row-level PHI by construction.
- Limitations (honest): JS/TS and Java SDKs are not yet generated (OpenAPI
  codegen covers them); webhook subscription management is API-level, no
  self-service portal UI yet; rate-limit numbers are operator-tuned, not yet
  published as SLAs.
