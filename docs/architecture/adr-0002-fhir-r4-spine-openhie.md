# ADR-0002: FHIR R4 spine with OpenHIE-aligned registries

- Status: Accepted
- Date: 2026-10-17
- Spec reference: §5.4

## Context

Health-IT interoperability in low-resource settings fails through fragmentation: 80+ parallel NGO mHealth projects, paper plus parallel spreadsheets, disconnected EMRs, and DHIS2 aggregates arriving months late. India's ABDM (975M+ ABHA IDs) and Indonesia's SatuSehat proved the registry+gateway pattern on HL7 FHIR R4 at population scale.

## Decision

1. **FHIR R4 is the single clinical data model.** Profiles for Patient, Encounter, Observation, Medication, Claim, Coverage and Consent are published as an open implementation guide (`fhir/`), Apache-2.0, ABDM-pattern.
2. **OpenHIE-aligned registries**: patient identity (NIN-bound where available, locally-resolvable offline ID otherwise), provider registry, facility registry, terminology service (ICD-11, LOINC, national code sets). Implemented in `services/registry-service`.
3. **FHIR core is composed, not built**: Medplum (Apache-2.0) provides the server, tenancy and auth delegation; we contribute Nigerian profiles upstream rather than forking.
4. **DHIS2 bridge** (`packages/dhis2-bridge`) keeps national aggregate reporting unbroken — facilities never dual-enter.
5. **Consent manager**: every cross-facility share requires a signed, revocable Consent resource (HIE-CM pattern).

## Consequences

- Third parties integrate via the same public FHIR APIs the core uses — the app-store model consolidates instead of fragmenting.
- Legacy paper/HL7v2/PDF inputs enter through a Metriport-pattern harmonization engine (pattern adapted, licence AGPL-compatible).
- Conformance suites in CI validate every profile on every merge; a broken profile fails the build.
