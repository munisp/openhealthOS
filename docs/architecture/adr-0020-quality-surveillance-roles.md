# ADR-0020: Executable quality measures, notifiable-disease surveillance, platform roles, and UI grammar

Status: accepted (2026-10-19)

## Context

The big multi-part request after dispatch: quality measures that make
care standard across locations following the FHIR IG; a disease
surveillance engine following the FHIR IG; a coherent answer to "does
the UI look the same everywhere"; and a catalogue of every platform
role with robust onboarding per role.

## Decisions

### 1. Quality is executable, not aspirational — `packages/quality`

A care standard in a PDF does not survive 774 LGAs. Measures are FHIR
**Measure** resources versioned as data in `fhir/measures/` (ANC 4+
visits, Penta-1 coverage, hypertension control, appointment show-rate,
emergency arrival-confirmed-within-SLA — the last closes the loop on
ADR-0019). A small auditable criteria engine evaluates them over the
SHR bundle exports facilities already produce, per facility per
period, emitting FHIR **MeasureReports** — same definition, every
location, so two facilities' numbers are comparable by construction.
Proportion measures support per-patient (distinct-subject) semantics;
a zero denominator reports data-absent, never a fake 0%. No CQL — the
bundled Nigerian priority measures need only declarative
path/op/value criteria, which a program officer can read and edit.

### 2. Surveillance: syndromic radar + IDSR case detection

`packages/surveillance` now has two halves. The existing syndromic
radar watches triage exhaust for clusters. The new
`notifiable.py` is the named-disease half: Nigeria IDSR case
definitions as data over FHIR Condition/Observation codes — cholera,
measles, CSM, Lassa, yellow fever, AFP/polio (immediate, 24h) and
severe malaria, TB (weekly aggregate). Lab observations upgrade
suspected → confirmed; a 28-day episode window dedupes referral
re-entries; immediate alerts ride the critical queue as
`surveillance_alert` events; aggregation is k-anonymity-suppressed
counts — epidemiology leaves the facility, identity never does.

### 3. Roles and onboarding — `docs/roles.md` + registry `/onboarding`

Fourteen roles catalogued (patient through auditor) with their data
sensitivity. Staff onboarding is one five-step chain — invite →
identity-check → credential-verify → facility-assign → activate —
driven by `RoleMatrix`, role → required-steps **as data**: licensed
cadres (physician, nurse, midwife, pharmacist, lab scientist, CHEW)
cannot activate without council verification (provcred seam);
dispatchers, officers, and auditors skip credential-verify but never
identity. Anonymous onboarding is refused (ADR-0018's named-actor
rule). Suspension names who and why and never deletes. Patients are
not onboarded by staff — self-registration plus the KYC ladder.

### 4. UI grammar — `apps/design-tokens.css`

Care-facing surfaces (patient app, clinician PWA, Android shell via
the PWA) share one Medplum-inspired grammar — white cards, soft
shadow scale, pill badges, indigo accent — from a single token file
linked before each app's stylesheet and precached by both service
workers. Palettes may differ per audience; shape may not. The
operations dashboards' dark theme is a deliberate situation-room
choice, documented and exempt. `docs/ui-consistency.md` is the audit;
the WCAG contrast gate covers every stylesheet.

## Consequences

- The IG (`fhir/implementationguide.json`) now registers the five
  executable measures alongside the four profiles; emergency care maps
  to `Encounter.class = EMER` with no new licensing surface.
- Quality, surveillance alerts, and dispatch outcomes all converge on
  the same notification rail and the same MeasureReport format — the
  LGA officer's scorecard is one query, not four systems.
- Everything is stdlib (Go services, Python packages), offline-first,
  and licensed clean — no CQL engine, no embedded surveillance suite.

## Honest limits

- The criteria engine covers proportion measures over bundle exports;
  truly complex CQL measures would need an upstream engine later —
  composed, never embedded (ADR-0005).
- IDSR thresholds and code lists are pilot starting points; states tune
  the JSON, not the code.
- RoleMatrix is in-memory in the registry today; the Postgres
  migration follows the same parity pattern when a state needs
  durable onboarding records at scale.
