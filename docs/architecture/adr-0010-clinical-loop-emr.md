# ADR-0010: Clinical loop — pharmacy module, EMR depth in the PWA, Medplum as the production FHIR foundation, OpenMRS/Bahmni as peers

**Status**: accepted · **Date**: 2026-10-18

## Context

The platform had the money loop (claims → adjudication → payment) and the
care rails (telehealth, LIS bridge, SHR) as separate seams, but the
clinical loop was not closed: a prescription written in a telehealth
consult went nowhere, a lab result stayed in the LIS bridge, and there
was no dispensing or pharmacy stock story at all. Three architectural
questions had to be settled together:

1. **What is the FHIR core?** shr-service is our stdlib, contract-only
   stand-in (envelope `PUT /fhir/{type}/{id}` + consent gate). It proves
   the contract but is not a full FHIR server (no full search,
   subscriptions, or compartment enforcement).
2. **Where does the EMR live?** Nurses and CHWs work offline at PHCs
   (ADR-0001, ADR-0009); hospitals already run OpenMRS/Bahmni.
3. **Do we re-implement OpenMRS/Bahmni on our stack?** That would be a
   rewrite of a mature hospital system — the compose-don't-fork policy
   (ADR-0005) says no.

## Decision

**Medplum is the designated production FHIR foundation; shr-service
remains the edge/dev seam; the EMR lives in the PWA; OpenMRS/Bahmni are
interoperable peers, never rewrite targets.**

1. **Clinical loop wiring (closed loop, auditable at every hop).**
   - Telehealth close/escalate → FHIR `Encounter` written to the SHR
     (best-effort: care never blocks on the write failing; sync replay
     catches up).
   - Telehealth prescribe → FHIR `MedicationRequest` to the SHR **and**
     a dispense-request routed to the pharmacy service.
   - Pharmacy (`services/pharmacy-service`) holds the tracer-drug
     formulary (10 essential medicines with `allergy_class`), stock per
     facility, and dispenses **atomically**: guarded status flip +
     conditional stock decrement + NHIA claim draft in one transaction —
     stock can never go negative, a request can never be dispensed twice,
     and every dispense starts a claim.
   - The **allergy gate is structural**: a prescription whose drug
     allergy class matches the patient's recorded allergy is rejected at
     routing with the reason recorded — it is never silently dropped and
     cannot be dispensed (double-dispense of a rejected request fails
     closed).
   - LIS bridge pushes resulted `Observation`s to the SHR with facility
     provenance; push failure never blocks result delivery to the
     ordering facility.
2. **Medplum in the platform plane, profile-gated**
   (`infra/compose/platform.yaml --profile medplum`, with its own
   Postgres + Redis). `tools/medplum-validate` uploads the four OpenHealth
   StructureDefinitions and runs `$validate` over every example
   (positives must pass, negatives must fail) against the live server —
   the profiles are proven on the production foundation, not only on our
   stdlib validator. The envelope contract telehealth/lis-bridge speak
   maps onto plain FHIR REST, so the writers target Medplum unchanged
   when it fronts the SHR.
3. **EMR depth lives in the PWA** (`apps/pwa/js/emr.js` +
   `clinical.js`): patient chart with a unified timeline (encounters,
   vitals, labs, meds, appointments), vitals capture with WHO/IMCI danger
   bands (age-aware fast-breathing cutoffs, SpO₂, BP crisis, MUAC
   SAM/MAM), CPOE-lite lab orders (10-test catalog, `lab_order` events
   the LIS bridge consumes), prescribing with the same allergy gate on
   the edge, appointments with today's queue, and cohort tags (ANC/EPI
   derived; HIV/TB/NCD clinician-set). All of it works offline and in the
   five product languages.
4. **OpenMRS/Bahmni bridge** (`packages/adapters/openmrs.py`): pulls
   Patient/Encounter/Observation from the hospital's FHIR2 module into
   the SHR envelope (provenance tagged `openmrs`), pushes outbound
   resources (referrals, synced patients) back. Hospital keeps its
   system; the patient's record is whole.

## Consequences

- The pharmacy becomes the third money-producing rail: every dispense
  drafts a claim (feeding the claims orchestrator), making stockouts and
  revenue leakage visible in the same loop.
- Allergy safety is enforced twice — at the point of prescribing (PWA
  blocks, loud) and at the counter (pharmacy rejects, recorded) — because
  either side can be offline when the other acts.
- Medplum runs its own database; the platform state plane and the FHIR
  foundation scale and fail independently. The consent gate stays
  platform-owned policy (shr-service / consent-service) — Medplum is the
  store, not the policy engine.
- Verified by `demo/clinical-loop.sh`: consult → assess → prescribe →
  SHR (MedicationRequest + Encounter) → pharmacy pending → dispense →
  stock 100→76 + ₦2,880 claim draft → allergy-blocked second patient
  rejected with reason. CI runs the unit/integration suites; the smoke
  script is the operator-facing acceptance check.
