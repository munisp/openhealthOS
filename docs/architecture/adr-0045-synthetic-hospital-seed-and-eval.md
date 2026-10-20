# ADR-0045: Synthetic Hospital corpus as seed data + standing clinical-AI eval harness

Status: accepted (2026-10-20)
Source: Park, Chen, Dettmers — *Synthetic Hospital: An Open, Verifiable,
Physician-Validated Longitudinal EHR Benchmark* (arXiv:2609.30027)

## Context

Three chronic gaps this paper closes for us:

1. **No lawful demo/CI/training data.** NDPA 2023 bars real charts in
   dev; our fixtures are hand-written and thin. Synthetic Hospital ships
   1,268 longitudinal patients / 5,602 notes, fully synthetic and openly
   redistributable, physician-validated as indistinguishable from real
   charts (53% vs 50% chance in blinded review), every fact grounded in
   ICD-10-CM / SNOMED CT / LOINC with a complete provenance chain.
2. **No benchmark gate for clinical-LLM features.** The paper's headline
   result is a warning we should institutionalise: across 10 frontier
   models the best longitudinal problem-list reconstruction is 0.73
   severity-weighted F1 (physician mean on a matched subset; best
   physician 0.89), and models miss ~half of clinically relevant
   findings when summarising a chart. Any summarisation/retrieval
   feature we ship needs a scored harness BEFORE production, and the
   paper provides one (four tasks, ground truth, public/heldout/train
   splits, HTTP scorer).
3. **A provenance doctrine worth copying.** Their "build the patient
   before the chart" pipeline (knowledge graph first, prose rendered
   last) is the same philosophy as our audit lake: facts are grounded,
   not inferred from generated text.

## Decision

- **`packages/synthseed`** converts `patient_profiles.jsonl` into FHIR R4
  transaction bundles (Patient/Encounter/DocumentReference/Condition,
  ICD-10-CM-coded) and loads them into shr-service. IDs are
  deterministic (sha256-namespaced) so re-seeding is an idempotent
  upsert; every Patient carries a `synthetic-hospital` provenance meta
  tag so synthetic data can never masquerade as a real chart downstream.
  Field-alias tolerance — the corpus schema has evolved across releases.
- **Eval harness doctrine:** any future clinical-LLM feature
  (summarisation, evidence retrieval, imaging-indication pre-reads)
  ships with a Synthetic Hospital score on the public split in its PR.
  The paper's own numbers set the floor: below-physician-mean F1 is a
  conversation, not a launch. Agents get extra scrutiny — the paper
  found multi-turn agents LOST 0.07–0.19 F1 when context was already
  available (agency pays only when evidence must be gathered, +0.14–0.34
  on longitudinal diagnosis).
- **What we do NOT adopt:** the corpus is USMLE/US case-mix (endocrine
  and circulatory over-weighted vs Synthea, and explicitly not
  population-calibrated). It seeds demos and tests — it is never used
  for Nigerian epidemiological inference, surveillance baselines, or
  tariff calibration. Maternal-health and infectious-disease mixes need
  Nigerian-grounded supplements before any local model evaluation.

## Consequences

- `demo/seed-synthetic.sh` (see packages/synthseed) gives pilots and CI
  realistic longitudinal charts in one command, zero legal review.
- SHR gains bulk test data exercising consent, break-glass audit, and
  FHIR surface under realistic volume.
- The provenance-tag convention extends our existing audit doctrine to
  seeded data: provenance is a first-class field, not a comment.
