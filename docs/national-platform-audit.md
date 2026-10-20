# OpenHealth OS — National-Platform Audit & Recommendations

Date: 2026-10-20 · Scope: full working tree (28 Go services, 52 Python packages,
3 JS apps + 2 native shells, 55 ADRs, benchmark harness, infra plane).

This audit asks one question: **what would make OpenHealth OS the premiere,
Swiss-army-knife nationwide healthcare platform** — the system a Ministry of
Health, an HMO, a state hospital board, and a rural CHW all reach for first?
Findings are grouped by plane; every recommendation states the gap it closes,
the doctrine constraints it must respect (offline-first, fail-soft, stdlib-Go
hot path, OSI-clean licensing, approval-gated AI writes), and a priority.

Grade legend: ✅ strong · 🟡 present but thin · 🔴 gap.

---

## 1. What is already premiere-grade

| Plane | Evidence | Grade |
|---|---|---|
| Offline-first edge | CRDT sync, store-and-forward, HTTP/3+QUIC at Caddy (ADR-0053), tc-netem loss-profile benchmarks | ✅ |
| Money integrity | Double-entry ledger with durability ADRs, fraud ML, anti-collusion sampling | ✅ |
| FHIR spine | R4 SHR, IPA conformance set, Bulk $export, IPS + SMART Health Links, immunization IG (ADR-0054) | ✅ |
| QR trust fabric | Keyring rotation, revocation, payload budget doctrine, browser-side WebCrypto verification, forensics (ADR-0055) | ✅ |
| Identity & security | Keycloak SMART scopes, step-up re-auth, production security baseline | ✅ |
| Scale evidence | Million-claim adjudication benchmark, encounter-indexed linkage | ✅ |
| Channel diversity | PWA, patient app, USSD, WhatsApp gateway, sovereign Matrix (howUdeyChat), voice stacks | ✅ |
| Licensing hygiene | LICENSING.md + ADR-0005 doctrine, compose-don't-fork, no BSL/SSPL/Elastic embedding | ✅ |

The foundation is genuinely rare: no other open platform combines offline-first
FHIR, a double-entry claims ledger, and a QR trust fabric. The gaps are no
longer architectural — they are **depth, integration, and proof**.

---

## 2. Clinical depth (the "hospital actually runs on it" gap)

🟡→✅ targets:

1. **CPOE + e-prescribing with decision support** (P0). Orders exist via
   clinical-loop EMR, but there is no order-set engine, no drug–drug /
   drug–allergy interaction checker, no dose-range validation against a
   formulary. Recommend: `packages/orders/` (order sets, status machine),
   `packages/interactions/` (open formulary — WHO EML + NAFDAC registry,
   interaction rules as data, not a licensed database), pharmacy-service
   hook. Fail-closed on allergy, fail-soft on interaction (warn-and-override
   with reason capture).
2. **Clinical documentation structured templates** (P0). Notes → structured
   OBS/procedure templates per specialty (ANC card, under-5 card, TB DOTS,
   surgical safety checklist). WHO ANC/IMCI digital kits as conformance
   targets, same doctrine as NPHCDA immunization contract.
3. **Laboratory full loop** (P1). `lis`/`lis-bridge` exist; add analyzer
   auto-verification rules, delta checks, critical-value alerting into
   notification-service + Matrix escalation, reflex testing rules.
4. **Blood bank ↔ theatre ↔ inpatient closed loop** (P1). Crossmatch records,
   transfusion reactions (AEFI-style bundle), implant/prosthesis tracking in
   theatre with the same HMAC tag fabric as equipment.
5. **Antenatal & maternal pathway** (P1). Nigeria's mortality burden makes
   this the highest-clinical-value specialty: ANC schedule engine (mirror of
   immig defaulter logic), danger-sign triage, obstetric referral integration
   with dispatch-service, partograph capture (structured Observation set).
6. **Chronic disease registries** (P2). HIV (APN/viral-load cascade),
   hypertension/diabetes cohorts with medication-adherence scoring feeding
   pophealth + payrails (capitation quality adjustments).

## 3. National-scale interoperability

1. **Nigeria national rails** (P0): NIN verification adapter (fail-soft,
   cached attestation), NHIA capitation/FFS submission format conformance,
   state HMO scheme maps. Currently identity is Keycloak-internal; national
   rollout needs the civil-identity bridge. Adapter pattern, never hot-path.
2. **Terminology service** (P0). A `services/terminology-service` —
   SNOMED-CT (license-gated, loaded by the deployer), LOINC, ICD-10/11,
   NAFDAC drug codes, NHIS tariff codes — with $lookup/$validate-code and
   offline snapshots synced to edge. Every other plane depends on this.
3. **Inter-facility record exchange** (P1). SHL mint/fetch is per-patient;
   add facility-to-facility referral bundles (consent-gated DocumentReference
   exchange through referral-service), and SHR-to-SHR federation for
   state/national tiers (OpenHIE HIE pattern, already ADR'd — implement).
4. **HMIS/DHIS2 bidirectional** (P1). dhis2-bridge is outbound; inbound
   (org-unit registry, facility master list as source of truth) closes the
   loop. Also WHO SMART Guidelines DAK measure libraries for indicator
   definitions-as-code.
5. **HL7v2 ingest** (P2) for legacy LIS/RIS at teaching hospitals — the
   serial/HL7v2 edge adapter deferred from ADR-0052.

## 4. Patient & citizen experience

1. **USSD/voice depth** (P0). ussd-gateway exists; expand to full
   self-service: appointment booking, drug-refill reminders, benefit-balance
   check, results-ready callbacks. Voice IVR in ha/yo/ig/pcm via
   voicebridge — this is the true inclusion rail.
2. **Caregiver/proxy access** (P0). Children, elderly, incapacitated —
   delegated access with scoped consent in consent-service; absence of proxy
   access blocks half of real-world usage.
3. **Medication adherence rail** (P1). Refill scheduling, pickup QR (reuse
   OHMEM1 fabric), community-pharmacy redemption, adherence scoring.
4. **Digital therapeutics & wellness content** (P2). wellness-service exists;
   add guideline-based content packs (hypertension, diabetes, maternal) with
   offline caching and language packs.
5. **Transport/ambulance patient-facing ETA** (P2) on top of
   ambulance-map.js + dispatch.

## 5. Intelligence & assurance

1. **CDS hooks** (P0). A `services/cds-service` implementing the CDS Hooks
   spec — the standard way to plug alerts (interactions, due vaccines,
   danger signs, fraud flags) into every UI at once. All rule engines we
   already have (immig defaulter, fraud ML, interactions) become hook
   providers.
2. **ML registry operationalization** (P1). ml/registry exists; wire model
   versioning + shadow-mode evaluation + promotion gates into the
   adjudicator (approval-gated, per ADR-0046).
3. **Data quality plane** (P1). Completeness/timeliness/validity scoring per
   facility per indicator, surfaced in dashboards — the single biggest
   driver of trust in national HMIS data.
4. **Antimicrobial stewardship + pharmacovigilance** (P2). Aggregate
   prescribing analytics; adverse-event reporting bundles to NAFDAC format.
5. **Outbreak early-warning** (P2). surveillance exists; add
   anomaly-detection on syndromic feeds with geospatial clustering
   (gisexport + pophealth).

## 6. Operations & enterprise

1. **HR/staff registry + credentialing** (P1). rota exists; add licensure
   verification (MDCN/NMCN numbers), credential expiry alerts, and link
   prescriber identity to CPOE and to the QR verification-key provisioning
   (MDM story).
2. **Procurement & supply chain closed loop** (P1). procurement +
   stocktake + coldchain exist; add demand forecasting (consumption-based
   from pharmacy dispensing), LMS (logistics management information) push
   to state stores, and cold-chain excursion alerts into CMMS work orders.
3. **Facility licensing & accreditation module** (P2). Inspection
   checklists, scoring, renewal workflows — makes the platform the
   regulator's tool too.
4. **Multi-tenancy hardening** (P1). State-tier vs facility-tier isolation
   proofs, per-tenant Keycloak realms, row-level security audit in Postgres
   migrations, tenant-scoped encryption keys.

## 7. Platform engineering & proof

1. **Load/chaos evidence beyond claims** (P0). benchmark/ covers claims +
   edge latency; add sync-storm simulation (1000 facilities reconnecting),
   ledger throughput under contention, and a chaos runbook (kill Postgres,
   kill edge, verify fail-soft honest reporting).
2. **Blue/green & migration rehearsal** (P1). db/migrations is linear; add
   expand-contract discipline + rehearsal tooling, documented in runbooks.
3. **SBOM + signed releases** (P1). CycloneDX SBOM per release, cosign
   signatures, dependency licence gate in CI (extends ADR-0005 doctrine to
   automation).
4. **Native shells parity** (P2). android-shell/ios-shell are thin; decide
   PWA-first officially (recommended — offline + network-constrained
   doctrine favours it) and keep shells as wrappers, or invest.
5. **Developer experience** (P2). openapi/ exists; generate typed clients,
   postman/insomnia collections, and a "spin up a synthetic state in 5
   minutes" demo (synthseed already gives the data).

---

## 8. Recommended sequencing (next 4 iterations)

| Iter | Theme | Items |
|---|---|---|
| 56 | Clinical trust | CPOE + interactions + formulary; CDS hooks service; proxy/caregiver consent |
| 57 | National rails | Terminology service; NIN adapter; NHIA format conformance; DHIS2 inbound |
| 58 | Depth | Maternal/ANC pathway; lab auto-verification + critical alerts; HR credentialing |
| 59 | Proof | Sync-storm + chaos benchmarks; SBOM/signing; data-quality plane dashboards |

Each lands as an ADR with the same discipline as 0052–0055: implementation,
tests, honest limitations, cross-platform integration (middleware, services,
PWA/patient/dashboards), and a full sweep before push.
