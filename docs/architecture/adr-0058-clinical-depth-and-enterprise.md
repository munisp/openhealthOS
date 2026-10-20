# ADR-0058: Clinical depth & enterprise — ANC, lab loop, HR credentialing, registries, stewardship, accreditation, tenancy

Date: 2026-10-20 · Status: accepted

## Context

Audit iterations 58 scope: the maternal pathway (Nigeria bears ~20% of
global maternal deaths), the lab closed loop, staff credentialing, chronic
registries, antimicrobial stewardship, facility accreditation, and
multi-tenancy proofs.

## Decision

1. **ANC (`packages/anc`)** — WHO 2016 eight-contact schedule with
   due/overdue/defaulter logic mirroring immig; Naegele EDD; danger-sign
   triage (critical first, honest "unmapped" fallback); partograph alert-line
   detection (1 cm/hr from 4 cm).
2. **Lab closed loop (`packages/lis/labloop.py`)** — auto-verification in
   range; **critical values fail-LOUD** (CLI exit 1, alert payloads for
   notification rails); delta checks vs prior results; reflex suggestions
   (e.g. high glucose → HbA1c); unknown analytes go to review, never
   silently pass.
3. **HR credentialing (`packages/hrcred`)** — council licence formats
   (MDCN/NMCN/PCN/MLSCN/CHPRBN), expiry/suspension gates; consumed by CPOE
   prescriber checks and by QR verification-key provisioning (ADR-0055).
4. **Chronic registries (`packages/chronicreg`)** — HIV cascade
   (diagnosed→suppressed), hypertension, diabetes; evidence-gated stage
   transitions; adherence scoring feeding payrails quality adjustments.
5. **Stewardship (`packages/steward`)** — WHO AWaRe classification with the
   ≥60% Access target; IV→oral switch flags; NAFDAC-format ADR reports.
6. **Accreditation (`packages/accredit`)** — weighted inspection checklist,
   A–D grades, licence actions (renew/conditional/suspend), renewal-due
   alerts — the regulator's view of the same data.
7. **Tenancy (`packages/tenantguard`)** — static audit of migrations for
   facility/tenant scoping on PHI/money tables (CI gate, exits 1) plus a
   runtime row-isolation guard. Current migrations pass.

## Consequences

- Positive: the platform now covers the clinical pathways that dominate
  Nigerian mortality and morbidity, and the regulator/payer/chief-medical-
  director personas all have first-class tooling.
- Limitations (honest): AWaRe and interaction tables are curated subsets;
  ANC danger-sign actions are protocol text, not automated dispatch (the
  dispatch hook is the next iteration).
