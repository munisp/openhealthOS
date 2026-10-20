# ADR-0057: National rails — terminology, NIN, NHIA, DHIS2 inbound, facility identity, federation

Date: 2026-10-20 · Status: accepted

## Context

National rollout needs civil-identity bridging, payer submission formats, a
shared terminology plane, bidirectional HMIS, and — most basic of all — a
governed answer to "does each facility have a unique identifier?" (Until now:
no; facilities were free-text strings like "FAC-1".)

## Decision

1. **Terminology service (`services/terminology-service`)** — FHIR R4
   `$lookup` / `$validate-code` / `$expand` over JSON code-system snapshots
   in `data/terminology/` (ICD-10 core, NAFDAC formulary codes, NHIA tariff
   codes ship openly; SNOMED-CT is licence-gated and loaded by the deployer).
   Edge facilities sync signed snapshots (`/snapshots/manifest` +
   deterministic hash) for offline validation.
2. **Canonical facility registry (`packages/facilityreg` + migration 0018)**
   — OHF-<state>-<lga>-<seq> ids minted only by the registry; DHIS2 uid /
   HMIS / NHIA provider codes are mapped attributes, never identity.
   Duplicates: same DHIS2 uid is refused; same name+LGA is minted but
   flagged for steward adjudication.
3. **NIN adapter (`packages/nin`)** — verifier callable injected (NIMC in
   production); 30-day cached attestations; raw NIN never stored
   (SHA-256 fingerprint — NDPA minimization). **Fail-soft: unverified is a
   state, not a block; unreachable upstream falls back to last-known.**
4. **NHIA conformance (`packages/nhia`)** — FFS + capitation submission
   envelopes; per-claim validation with tariff-code checks; invalid claims
   rejected individually while the submission continues (fail-soft);
   deterministic submission hash for reconciliation.
5. **DHIS2 inbound (`packages/dhis2-bridge/inbound.py`)** — org-unit pull
   upserts the facility registry (updates, never duplicates); unreachable
   upstream replays the last-good snapshot from disk.
6. **SHR federation (shr-service)** — `POST /federation/bundle` builds a
   patient's transaction Bundle for a named peer ONLY with verified consent
   (**fail-closed**: no consent / consent-service down → 403 with honest
   reason); peer manifest via `GET /federation/peers`.

## Consequences

- Positive: platform speaks the national languages (identity, payer, HMIS,
  terminology) and every asset/claim/presence record can now key on a real
  facility id.
- Limitations (honest): NIMC/NHIA/DHIS2 endpoints are adapter-shaped — field
  mapping against live national sandboxes is a deployment task; federation
  is bundle-push, not yet a query-mediated HIE.
