# Consent service (Go) — HIE-CM pattern

Every cross-facility record share requires a **signed, revocable consent
artefact** (§5.4, NDPA 2023). Artefacts are HMAC-signed over their canonical
fields — tampering invalidates them. Revocation is immediate and permanent.
Every issue/check/revoke lands in an append-only audit log.

- `POST /consents` — issue (returns signed artefact)
- `POST /consents/{id}/revoke` — revoke (idempotent)
- `POST /consents/check` — enforcement question: `{patient_id, grantee, scope}` → `{allowed}`
- `GET /audit` — the immutable lifecycle log

Production: set `CONSENT_HMAC_KEY`; bind Postgres behind the manager seam.

```bash
go run . && go test ./...
```
