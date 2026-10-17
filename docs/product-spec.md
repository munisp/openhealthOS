# Product specification — the operating system

*Condensed from OpenHealth OS Business · Technical · Architectural Specification v1.0 (October 2026). Nigeria pilot — African-country scale.*

## Design principles

1. **Open source first, forever.** Core AGPL-3.0; SDKs/adapters Apache-2.0; Digital Public Goods recognition. No facility ever pays to be counted.
2. **Offline-first is a right, not a feature.** Every clinical, enrolment and claims workflow runs with zero connectivity.
3. **The physical layer is in scope.** Power, water, cold chain and equipment are monitored and maintained through the platform.
4. **FHIR R4 and OpenHIE alignment** — the standards India's ABDM and Indonesia's SatuSehat run on.
5. **Federal by design.** National spine (registries, standards, consent) + state-operated instances; no data flow a state doesn't authorise.
6. **Nurses and CHWs are the primary users** — task-typed, protocol-guided, in Hausa, Yoruba, Igbo, Pidgin and English.
7. **Frugal hardware.** Runs on a ₦1.8M ($1,200) edge node and five-year-old Android phones.
8. **Money rails are part of health infrastructure.** Claims, pre-authorisation, payments and procurement are native events, not PDFs.
9. **Privacy by architecture.** NDPA 2023 compliant; patient consent is a signed digital artefact, revocable and auditable.
10. **Composable, not monolithic.** Any module can be replaced; anything can build on the public APIs.

## Personas

| Persona | What they do in the OS |
|---|---|
| PHC nurse / CHO | Registers patients, runs protocol-guided encounters, dispenses, refers; works offline all day |
| Community health worker | Household enrolment, ANC and immunisation task lists, eligibility checks via USSD offline |
| Facility manager | Stock, equipment tickets, power/water telemetry, staff rota, revenue dashboard |
| State insurance officer (SHIA/HMO) | Eligibility verification, pre-authorisation, claims adjudication, provider payments, fraud flags |
| State commissioner / NPHCDA | Live dashboard of effective coverage, stockouts, facility readiness, BHCPF utilisation |
| Patient | USSD/phone eligibility, appointment and refill reminders, portable record via consented sharing |

## Module map (twelve modules, four groups)

1. **Identity & registries** — NIN-bound patient ID with locally-resolvable offline scheme; provider registry (licences, specialties); facility registry (ownership, services, power/water/equipment profile); terminology service (ICD-11, LOINC, national code sets). OpenHIE-style APIs. → `services/registry-service`
2. **Core health record** — FHIR R4 shared health record, consent-managed sharing; OpenMRS/Bahmni-derived point-of-care EMR, fully functional offline. → `fhir/` + Medplum core
3. **Community health** — OpenSRP-pattern CHW app: household census, ANC/immunisation task lists, danger-sign triage, USSD fallback.
4. **Claims & insurance rails** — real-time eligibility, pre-authorisation, e-claims submission and adjudication for NHIA, state schemes and HMOs; BHCPF poor-and-vulnerable flows; NHCX-style gateway. Target: turnaround in days, not months. → `services/claims-orchestrator`, `packages/adjudicator`, `openapi/claims-gateway.yaml`
5. **Payments** — facility wallets, instant collection via Nigerian PSPs, premium micro-collections (₦500–2,000 instalments), automated provider payouts, reconciliation. → `services/ledger`
6. **Procurement marketplace** — framework-priced catalogue, pooled ordering, last-mile logistics, supplier fulfilment scorecards from stock-out telemetry.
7. **Laboratory** — OpenELIS-based LIMS, hub-and-spoke diagnostics, results as FHIR Observations.
8. **Telehealth + AI triage** — nurse-first teleconsults, protocol-guided triage tuned to Nigeria's top-20 presentations; **every AI write is approval-gated**. → `packages/triage`
9. **Infrastructure layer (the differentiator)** — solar/inverter/battery telemetry, water-tank levels, vaccine cold-chain temperature, generator runs; equipment registry with maintenance tickets routed to a vetted BMET marketplace; donation intake screening. → `services/telemetry-collector`
10. **Analytics & developer platform** — state/LGA dashboards, DHIS2 aggregate bridge, public FHIR APIs, webhooks, app store. → `packages/dhis2-bridge`

## Evidence base

The design imports three proven patterns: Rwanda's community insurance + CHW scale (life expectancy 49.7→69.6, >90% insured), India's ABDM registry+gateway trinity on FHIR R4 (975M+ health IDs), and the open commons (DHIS2 ~80 countries, OpenMRS across sub-Saharan Africa). What does not transfer from Rwanda: the strong central state — Nigeria is a federation, so the OS is interoperable state-level infrastructure with a national spine, not a monolith.

## Failure modes engineered against

The spec distils ten documented failure modes of open-source EHRs in low-resource settings (pilotitis, infrastructure mismatch, paper-digital dysfunction, skills shortage, documentation collapse, workflow misalignment, donor dependence, interoperability failure, trust deficit, emergency-timed deployments) into explicit counter-measures. The meta-lesson: software cannot manufacture political will, but it can make commitment cheap to sustain — low recurring cost, local ownership, measurable wins, and an exit path that never strands the state.
