# Security policy

Health data is attack surface; security operations are part of the product, not an afterthought.

## Reporting a vulnerability

**Do not open a public issue.** Email security@openhealthos.org with:

- affected component and version/commit
- reproduction steps or proof-of-concept
- potential impact (data classes touched: patient records, claims, payments, telemetry)

We acknowledge within 48 hours and aim to ship a fix or mitigation within 14 days for critical issues. Coordinated disclosure is the default; we credit reporters who want it.

## Baseline controls (NDPA 2023)

- Encryption in transit (TLS everywhere, including edge→core sync) and at rest.
- Role-based access down to field level; Keycloak OIDC for staff, patients and third-party apps.
- Every cross-facility record share requires a signed, revocable consent artefact.
- Immutable audit logs: every claim decision, payment posting and consent change is a replayable event.
- Edge fleet runs signed images, staged over the air; rollback is one command.
- Annual third-party penetration tests; path to ISO 27001 within 24 months of first deployment.

## Supported versions

Until v1.0, only the latest `main` and the most recent tagged release receive security fixes.

## Scope notes

- The mock payment switch (`services/payment-switch-mock`) is a **test fixture** — never deploy it as a settlement endpoint.
- Prototype seams are documented honestly in each service README; check before relying on any security property.
