# ADR-0054: FHIR IG conformance — NPHCDA/WHO immunization contract, IPA, Bulk Data, SMART Health Links

Status: accepted (2026-10-20)

## Context

The platform produced and consumed FHIR, but "speaks FHIR" ≠ "conformant
to a contract a counterparty can rely on." Four implementation guides
were evaluated and adopted; US-centric IGs (US Core, Da Vinci, CARIN,
mCODE) were rejected — US regulatory shape, no Nigerian mandate.

## Decision

### 1. National immunization contract — `packages/immig` (stdlib Python)

Clean-room implementation of the conformance contract behind the NPHCDA
Nigeria Immunization IG / WHO SMART DAK — the *checks*, not copied code:

- `validate_immunization` — status, vaccine code mappable to an EPI
  antigen, occurrence, patient reference; **lot number required on
  completed doses** (a vaccination without a lot can't be traced through
  a cold-chain failure); location-or-performer required.
- Full Nigerian EPI schedule (birth / 6 / 10 / 14 wk / 9 / 15 mo, 19
  lines) with per-antigen grace windows; `defaulter_status` turns a
  birth date + history into DUE/OVERDUE/DONE per antigen — a child two
  weeks late for Penta-3 is a CHW task, not a statistic.
- `build_aefi_bundle` — AEFI report as a FHIR transaction Bundle
  (Observation + Flag) POSTable to the SHR as-is.
- `dhis2_aggregate` — per-antigen period tallies that reconcile with
  dhis2-bridge exports: the state's numbers must equal what wards gave.

### 2. IPA — `packages/ipa` conformance harness + Keycloak SMART scopes

- `ipa.py check --base …` probes a FHIR server against the IPA v1.0.0
  support set (Patient, AllergyIntolerance, Condition, Medication*,
  Immunization, Observation vitals+labs, DocumentReference), checking
  the CapabilityStatement for SMART security, per-resource search+read.
  Empty-but-legal searches are reported *skipped, unverifiable* — never
  silently passed. Exit code non-zero on non-conformance: CI-usable.
- `realm-openhealth.json` gains SMART client scopes (smart-patient-read,
  smart-system-read, smart-launch) and two clients: `openhealth-patient`
  (PKCE public client for PWA/howUdeyChat) and `openhealth-bulk-export`
  (Backend Services client_credentials). Scope config is now a reviewed
  artifact, not console folklore.

### 3. Bulk Data — `services/bulk-export-service` (stdlib Go)

FHIR Bulk Data Access flow: `GET /fhir/$export?_type=…&_since=…` → 202 +
Content-Location; `/fhir/bulkstatus/{job}` → manifest; `/fhir/bulkfile/
{job}/{type}.ndjson` → `application/fhir+ndjson`. Pages the composed
Medplum SHR (200/page, follows `next` links), streams NDJSON to
EXPORT_DIR. Fail-soft per type (a failed type is a partial warning, not
a failed job; all-fail = failed). Unknown jobs after restart report **410
Gone, never phantom 200s**. Empty types leave no file and no manifest
entry. Path-escape guarded. Gateway routes `$export`/`bulkstatus`/
`bulkfile` ahead of the `/api/fhir` catch-all. NDJSON feeds lakehouse,
kpiplane, pophealth — the bespoke-ETL replacement.

### 4. IPS hardening — SMART Health Links + shared crypto

- `packages/ips/shl.py` — SHL payload mint/parse (`shlink:/` URIs,
  32-byte keys, exp, P/L flags), fail-closed parsing.
- `shared/shlx` (Go) — AES-256-GCM content-file encryption
  (`OHSHL1.<nonce>.<ct>` envelope; Go stdlib because Python stdlib has
  no AEAD). `tools/ipsqr` refactored onto it — one crypto seam.
- shr-service gains `GET /ips/{pid}/document` (builds the IPS from the
  consent-gated record via packages/ips subprocess, validates before
  serving) and `GET /ips/{pid}/shl` (mints a link; **the server stores
  only ciphertext — the key exists solely inside the link**) with
  `/shl-file/{id}` serving envelopes until expiry (410 after). Access
  uses the same patient-self/consent gates as `$everything`.
- **Documented divergence:** file encryption is our compact envelope,
  not full JWE — revisit when a counterparty requires JWE.

### 5. UI/UX

- Dashboards: new **Immunization** view — coverage per antigen, defaulter
  outreach list (14d+ late = crit), and a live Bulk $export card that
  kicks a real export and polls the job to manifest.
- Patient PWA: "Share my summary" card on the record tab mints a SMART
  Health Link via the SHR; offline-honest (no network → says so, never
  fakes a link).

## Consequences

- Immunization conformance is executable; defaulter tracking drives CHW
  outreach; AEFI reporting is a POST away; DHIS2 totals reconcile.
- IPA conformance is a CI gate, not a claim. Bulk export gives payers and
  regulators a standards-shaped on-ramp; IPS sharing works with any
  SHL-aware scanner.
- Rejected/deferred: US Core & Da Vinci family (US mandate shape);
  OpenHIE Case Reporting (architecture adopted, conformance deferred —
  IG still in development); SDC (when e-forms formalize); full JWE for
  SHL files (documented divergence).
- howUdeyChat hook deferred: defaulter outreach notifications over the
  chat rail are the natural next integration.
- Limitations: bulk-export job state is in-memory (files persist on
  disk); shl links are single-file, no manifest bundles yet.
