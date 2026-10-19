# ADR-0034: Population health — OMOP-lite, cohorts, care gaps, predictive risk

Status: accepted · 2026-10-19

## Context

The platform's analytics were facility- and claims-facing (ADR-0014,
0032, 0033). Population health asks different questions: *who* in the
panel is diabetic, who is overdue for screening, who is drifting into
high cardiovascular risk — across facilities, from the FHIR record the
SHR already holds. OHDSI/OMOP is the industry-standard common data
model for exactly this; predictive risk stratification is the standard
operating model on top.

## Decision

**`packages/pophealth` (stdlib-only)**, reading FHIR R4 bundles:

1. **OMOP-lite mapping.** OMOP CDM is a schema convention, not licensed
   code — we map FHIR (Patient, Encounter, Condition, Observation,
   MedicationRequest/Administration) onto person / visit_occurrence /
   condition_occurrence / measurement / observation / drug_exposure /
   observation_period tables (JSONL now; the same shapes become Parquet
   on the lakehouse silver tier in production). Concepts use an OH-lite
   namespace mapped onto LOINC/SNOMED/ICD source codes, exported as
   `concepts.json`; a full OHDSI vocabulary load replaces the map
   without touching the pipeline. Numeric Observations become
   `measurement`, everything else `observation` — the measurement table
   is what risk scoring and care-gap closure read.

2. **Cohorts as data.** COHORT_RULES: diabetes (E11*), hypertension
   (I10*), ANC (Z34*), under5, cvd_watch (≥40 or hypertensive). New
   registries are new rules, not new code.

3. **Care gaps as data.** CARE_GAP_RULES: who a rule applies to
   (age/sex/cohort), which observation closes it, how often it is due —
   BP check 12m for adults, HbA1c 6m for diabetics, cervical screening
   36m for women 25–49, immunization 6m for under5, ANC visit monthly
   for pregnancy. Never-done counts as due.

4. **Predictive risk, rules-first and honestly labelled.** The default
   CVD/diabetes scorers are transparent linear predictors through a
   sigmoid — directionally correct on age, SBP, BMI, diabetes,
   hypertension — carrying `model: "heuristic-1"` on every score.
   They order outreach lists; they never diagnose. The documented swap:
   a validated champion in the ml registry (ADR-0014) replaces the
   scorer wholesale via the bridge interface, and the label changes.

## Consequences

- A state desk gets registry counts, per-patient gap lists and risk
  bands from the same SHR export — no new infrastructure.
- The CLI (`omop|cohorts|gaps|risk|plan|all`) is the contract other
  services call (wellness-service's plan seam, ADR-0035).
- Licence posture unchanged: OMOP mapped clean-room, no OHDSI code.

## Verification

`packages/pophealth/test_pophealth.py`: 16 stdlib tests (mapping
fidelity incl. measurement/observation split, concept fallback to 0,
cohort membership, gap freshness vs staleness, never-done-is-due,
risk ordering, heuristic labelling, plan composition, CLI round-trips);
`demo/pophealth.sh` steps 1–2 run it against a realistic bundle.
