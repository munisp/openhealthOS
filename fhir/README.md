# OpenHealth OS FHIR R4 Implementation Guide (Apache-2.0)

Nigerian profiles on the FHIR R4 spine (ADR-0002), ABDM-pattern. Every
profile is a StructureDefinition JSON validated in CI on every merge.

## Profiles

| Profile | Base resource | Purpose |
|---|---|---|
| [openhealth-patient](profiles/openhealth-patient.json) | Patient | NIN-bound identity with offline-resolvable local id |
| [openhealth-encounter](profiles/openhealth-encounter.json) | Encounter | PHC encounter, offline-capture provenance |
| [openhealth-claim](profiles/openhealth-claim.json) | Claim | claims rails: pre-auth ref, adjudication status |
| [openhealth-consent](profiles/openhealth-consent.json) | Consent | signed, revocable cross-facility sharing artefact |

## Terminology

- ICD-11 for diagnoses, LOINC for observations, national code sets for
  benefits/tariffs (see `packages/adjudicator` fixtures for the working set).
- Facility-level codes: `https://openhealthos.org/fhir/CodeSystem/facility-level`
- Cadre codes: `https://openhealthos.org/fhir/CodeSystem/cadre`

## Conformance

CI validates that every file in `profiles/` is well-formed JSON with
`resourceType: StructureDefinition`, a canonical `url`, and derivation
`constraint`. The conformance surface grows with the implementation.
