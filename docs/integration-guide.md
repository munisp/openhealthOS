# Third-party integration guide — API & SDK

Date: 2026-10-20 · Companion: ADR-0061, openapi/claims-gateway.yaml,
packages/ohsdk. Audience: payers, insurers/HMOs, laboratories, pharmacies,
health-tech vendors, research/analytics consumers.

## 1. The integration model in one paragraph

Third parties integrate over **FHIR R4 APIs at the state-plane gateway**,
authenticated with **SMART Backend Services (OAuth2 client_credentials)**,
with **least-privilege scopes per stakeholder class**, **idempotency keys on
every mutation**, and **sandbox-first onboarding**. The Python SDK
(`packages/ohsdk`) wraps all of it; every API is also documented as OpenAPI
in `openapi/` for generating clients in any language. Third parties never
connect to facility edges or the national core directly — the gateway is the
only door, which is what makes the security model auditable.

## 2. Onboarding (the 5-step path)

1. **Apply** to the state plane operator (or NHIA for national scope) —
   organisation identity verified against the facility registry / CAC.
2. **Client provisioned** in Keycloak realm `openhealth` with a stakeholder
   profile (scopes below) — confidential client, secret rotated 90 days.
3. **Sandbox**: synthetic tenant seeded by `synthseed` — realistic data,
   zero PHI. Conformance suite must pass before production credentials.
4. **Production credentials** issued; mTLS optional at the gateway for
   payer-class clients.
5. **Go-live checklist**: idempotency discipline demonstrated, error
   handling honest (no silent retries), rate limits accepted.

## 3. AuthN/Z — SMART Backend Services

```
POST {keycloak}/realms/openhealth/protocol/openid-connect/token
grant_type=client_credentials&client_id=...&client_secret=...&scope=...
```

| Stakeholder | Scopes (least privilege) |
|---|---|
| Payer / insurer | `system/Claim.read system/Claim.write system/Coverage.read system/ClaimResponse.read` |
| Laboratory | `system/ServiceRequest.read system/DiagnosticReport.write system/Observation.write` |
| Pharmacy | `system/MedicationRequest.read system/MedicationDispense.write` |
| Health-tech vendor | per-partnership review; never `system/*.*` |
| Analytics / research | Bulk `$export` only, consent-scoped cohorts, k-anonymised |

Tokens are short-lived (5 min); the SDK caches and refreshes. Patient-context
SMART apps (patient-facing third parties) use the PKCE public client from
ADR-0054 instead — a different flow, not covered here.

## 4. The API surfaces

### 4.1 Claims gateway (payers / insurers) — `openapi/claims-gateway.yaml`
- `POST /api/claims/eligibility/check` — real-time coverage verification
- `POST /api/claims/preauth` — pre-authorisation with tariff code
- `POST /api/claims/submit` — NHIA-FFS envelope or FHIR Claim
- `GET  /api/claims/status/{id}` — adjudication status (eventually consistent
  by design: edge submissions may replay on reconnect)

### 4.2 FHIR resource APIs (labs, pharmacies, vendors)
- Labs: poll `GET /api/fhir/ServiceRequest?_since=...`, return
  `DiagnosticReport` + `Observation`. Critical values must ALSO honour the
  fail-LOUD callback contract.
- Pharmacies: `GET MedicationRequest/{id}` (CPOE order), then
  `POST MedicationDispense`.
- All FHIR writes pass fhir-validate; unknown profiles are rejected honestly.

### 4.3 Bulk analytics — `POST /api/fhir/$export`
202 → poll `bulkstatus` → NDJSON manifests (ADR-0054). Consent-scoped,
k-anonymity floor enforced in gold. This is the ONLY surface for analytics
consumers; there is no row-level query API for third parties.

### 4.4 CDS Hooks — `GET /api/cds/cds-services`, `POST /api/cds/cds-services/{id}`
Vendors embedding decision support consume the same hook bus the platform
UIs use (ADR-0056). Fail-soft: empty cards + warning means the provider is
degraded, not that the patient is safe.

### 4.5 Terminology — `/api/terminology/CodeSystem/$lookup`,
`ValueSet/$validate-code`, `ValueSet/$expand`
Validate tariff/diagnosis/drug codes BEFORE submitting claims — rejections
for unmapped codes are the integrator's bug, not ours.

## 5. SDK (packages/ohsdk)

```python
from ohsdk import Client
c = Client(base_url="https://gw.state.example",
           token_url="https://kc.example/realms/openhealth/protocol/openid-connect/token",
           client_id="hmo-acme", client_secret=..., 
           scope="system/Claim.read system/Claim.write system/Coverage.read")
c.eligibility_check("NG-0001")
c.preauth("NG-0001", "CONS-GEN", 1500.0, idem_key="acme-20261020-0001")
c.submit_claim(claim_doc, idem_key="acme-20261020-0002")
```

Doctrine baked into the SDK:
- **Idempotency keys mandatory on mutations** — the server dedupes; a 409
  raises `ConflictError` (the original landed; do not retry blindly).
- **Honest errors** — HTTP failures raise `SdkError` with the code; the SDK
  never silently retries into duplicates. Offline queuing is the facility
  edge's job, not the SDK's.
- Token caching with 30s safety margin.

## 6. Events / webhooks (push model)

Polling works, but payers usually want push. notification-service exposes
subscriptions: register a webhook per tenant for `claim.adjudicated`,
`preauth.requested`, `payment.reconciled`. Delivery is at-least-once with a
signed HMAC header (same keyring doctrine as ADR-0055); consumers must be
idempotent. USSD/SMS/WhatsApp notifications to members ride the same plane.

## 7. Rules of engagement

1. Sandbox conformance before production — non-negotiable.
2. Scopes are least-privilege; scope creep requires re-approval.
3. No scraping patient-context APIs with system tokens — detected anomalies
   suspend the client (fail-closed security).
4. Rate limits per tenant; 429 carries Retry-After — honour it.
5. Breaking changes ship versioned (`/api/v2/...`) with 12-month overlap;
   the OpenAPI specs and SDK version move together.
