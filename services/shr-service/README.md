# shr-service

Shared health record — FHIR R4 resource store with consent enforcement
on cross-facility reads (HIE-CM pattern, ADR-0002/0004).

The access rule is structural:

| Read | Decision |
| --- | --- |
| Same-facility | Allowed (treatment at source) |
| Cross-facility, active consent | Allowed, basis `consent` |
| Cross-facility, no consent | **Denied** — and the denial is audited |
| Break-glass emergency | Allowed, flagged `break-glass` for review |

Consent is checked against the consent service (`CONSENT_URL`). If the
consent service is unreachable or unconfigured, the SHR **fails closed**:
a missing consent check must never become an access grant.

Every access decision — allow, deny, break-glass — is an audit event
with actor, patient, resource, and legal basis. Writes are not gated:
consent governs reads, not the source facility's own record-keeping.

## API

| Route | Purpose |
| --- | --- |
| `PUT /fhir/{type}/{id}` | Upsert a FHIR resource (with provenance) |
| `GET /fhir/{type}/{id}` | Read one resource (consent enforced) |
| `GET /fhir/Patient/{id}/$everything` | Patient bundle as FHIR Bundle |
| `GET /audit` | Access log |
| `GET /healthz` | Liveness |

Reads require `X-Facility-ID`; emergencies set `X-Break-Glass: true`.

```bash
CONSENT_URL=http://127.0.0.1:8082 LISTEN=:8085 go run .
go test .
```
