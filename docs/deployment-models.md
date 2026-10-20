# Deployment models by stakeholder

Date: 2026-10-20 · Companion to ADR-0002 (federal model), ADR-0011 (national
scale), ADR-0042/0043 (edge + network plane), ADR-0057 (facility identity).

The platform has four physical tiers. Every stakeholder deployment is a
composition of them — nothing bespoke per stakeholder, which is what keeps
one codebase nationally deployable.

```
T0  National core        federal aggregates, terminology, DHIS2 bridge, NHIA hub (k8s)
T1  State plane          one k8s namespace+data plane per state (infra/k8s/*) — SHR,
                         claims, registry, consent, ledger; data never flows without
                         state authorisation
T2  Facility edge        docker-compose on a single box/mini-PC (infra/compose/edge.yaml)
                         — offline-first, CRDT sync to T1, HTTP/3 uplink
T3  Device / channel     PWA (staff), patient app, USSD/IVR, WhatsApp/Matrix, QR paper
```

Cost guardrail: $2.50/facility/month at state plane (Kubecost budgets).

---

## 1. Public hospital (secondary/tertiary, teaching)

**Model: T2 full edge node + T1 state plane services. Teaching hospitals may
host a T1 plane for their state.**

- Runs: full edge stack (gateway, SHR replica, ledger, pharmacy, imaging,
  IPD, equipment, LIS-bridge) on-premises; HL7v2/serial device adapters at
  the LAN; CPOE + CDS at the point of care; theatre/blood-bank modules.
- Connectivity: syncs to state plane when up; full clinical operation
  offline (ADR-0001). HTTP/3 uplink where 3G/4G is the only backhaul.
- Identity: facility gets a canonical OHF id (migration 0018); staff hold
  hrcred licences; verification keys provisioned to staff devices via MDM.
- Sovereignty: PHI stays in-state; national core sees aggregates only.

## 2. Clinic / PHC (primary care, ward level)

**Model: T2 lite edge (single mini-PC or even a hardened tablet hub) + CHW
PWAs at T3.**

- Runs: edge lite profile — registration, immunization, ANC, CPOE-lite,
  dispensing, presence QR, stock. No imaging/IPD workloads.
- Connectivity: assume 2G/2.5G days; everything queues; QR verification is
  fully offline (WebCrypto, ADR-0055). USSD covers feature-phone patients.
- Staffing reality: one nurse + CHEWs — the CDS bus pushes due-vaccine and
  ANC danger-sign cards instead of expecting protocol memorisation.

## 3. Private hospital

**Model: T2 edge + commercial tenancy on the state plane, or standalone
edge with national-gateway-only uplink.**

- Same edge image as public hospitals (one codebase); tenancy isolation
  enforced and audited by tenantguard (CI gate).
- Payer rails: NHIA FFS/capitation submission (packages/nhia) + private HMO
  scheme maps; their claims adjudicate through the same ledger with their
  scheme's tariffs.
- Opt-in data sharing: federation bundles to public referrals are
  consent-gated (fail-closed); aggregates to HMIS only per state mandate.

## 4. Laboratory (standalone / chain)

**Model: T3+ "lab node" — edge lite + lis/labloop; no full clinical stack.**

- Receives orders via FHIR ServiceRequest from referring facilities
  (federation); returns DiagnosticReport/Observation through the same rail.
- Auto-verification, delta checks, critical values fail-LOUD into the
  ordering facility's notification rail; reflex rules at the analyzer.
- Results-ready callbacks reach patients by USSD/SMS/WhatsApp; QR on the
  printed report verifies authenticity offline.
- Chain labs: one edge node per branch, roll-up to their head-office tenant.

## 5. Pharmacy (retail / community)

**Model: T3-first — PWA + QR redemption; optional edge lite for chains.**

- Verifies OHMEM1 member cards offline (WebCrypto, staff-provisioned keys);
  redeems e-prescriptions against the CPOE order id; dispensing events sync
  to the state ledger when online.
- Adherence snapshots feed the chronic-registry lakehouse feed (aggregate,
  k≥5) — the pharmacy becomes an adherence sensor without seeing full records.
- NAFDAC ADR reporting built into the dispensing screen (packages/steward).

## 6. Government agencies

| Agency | Model | What they run |
|---|---|---|
| **Federal MoH / NPHCDA** | T0 national core | Terminology service, DHIS2 bridge, immunization/AEFI aggregates, outbreak early-warning dashboards (aggregate only — no row-level PHI) |
| **State MoH / Hospital Mgmt Board** | T1 state plane operator | GitOps-managed state namespace, facility registry steward (duplicate adjudication), KPI plane, chaos-gate sign-off |
| **NHIA / State HMOs** | T0/T1 tenant | Claims submission conformance (nhia package), adjudication analytics, capitation quality adjustments from chronicreg gold, fraud ML outputs |
| **NAFDAC** | T0 tenant | Pharmacovigilance ADR inflow, AWaRe stewardship analytics, drug-code registry co-stewardship with terminology service |
| **Regulator (licensing)** | T1/T0 tenant | Accreditation module: inspection checklists, A–D grading, licence renewals — the same data facilities already produce |
| **NIMC** | External dependency | NIN verification adapter consumes their API; fail-soft cached attestations when unreachable |

## 7. Other stakeholders

- **HMOs / payers (private)**: T1 tenant; their scheme map + tariffs in the
  adjudicator; benefit visibility to members via USSD/PWA.
- **CHWs**: pure T3 — offline-first PWA, QR verification keys via MDM,
  task-typed views; household registry syncs upward.
- **Patients**: T3 — patient PWA, USSD self-service, WhatsApp/Matrix
  channels, SMART Health Links for portability. Patient devices never hold
  verification keys (ADR-0055).
- **Ambulance / EMS**: T3 dispatch app + ambulance-map; GPS telemetry to T1.
- **Universities / training**: training-service tenant on T1; synthetic
  hospital seed (synthseed) gives realistic data with zero PHI.
- **NGOs / donors**: read-only gold dashboards; no row-level access, ever.
- **Researchers**: OMOP silver + gold exports under k-anonymity; Bulk
  $export for approved studies with consent-scoped cohorts.

---

## Deployment topologies at a glance

| Stakeholder | T0 | T1 | T2 | T3 | Offline autonomy |
|---|---|---|---|---|---|
| Public hospital | | ✓ (consumes) | ✓ full | ✓ | Days–weeks |
| Clinic/PHC | | ✓ | ✓ lite | ✓ | Weeks (QR offline forever) |
| Private hospital | | ✓ tenant | ✓ full | ✓ | Days–weeks |
| Laboratory | | ✓ | lite | ✓ | Days |
| Pharmacy | | | opt. lite | ✓ primary | QR/dispensing queue offline |
| Fed/State agencies | ✓ / operator | ✓ | | dashboards | n/a (aggregate) |
| CHW / patient | | | | ✓ | PWA offline-first |

## Rules that never bend

1. One codebase; stakeholder differences are profiles, not forks.
2. PHI never leaves the state plane without authorisation; T0 is aggregate-only.
3. Every edge keeps working when the uplink dies; fail-soft with honest
   staleness everywhere, fail-closed only for security/consent.
4. No BSL/SSPL/Elastic components anywhere in any tier; deployer-licensed
   assets (SNOMED, drug compendia) are loaded by the deployer, never embedded.
