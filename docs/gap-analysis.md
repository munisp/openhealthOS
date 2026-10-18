# Gap analysis — OpenHealth OS as the national healthcare operating system

Assessment date: 2026-10-19. Scope: everything a country needs to
administer healthcare — clinical, financial, logistical, workforce,
emergency, intelligence. For each domain: what the platform already
does, what was missing, and where it landed.

## Legend

- ✅ shipped and demo-proven
- 🆕 shipped in the ADR-0021 wave
- 🗺️ roadmap — honest about what is not built

## Domain coverage

### Clinical operations
| Capability | Status | Where |
|---|---|---|
| Patient record / SHR / FHIR IG | ✅ | shr-service, fhir/profiles, Medplum composition |
| Scheduling, waitlists, theatre | ✅ | scheduling-service (ADR-0017) |
| IPD: wards, beds, ADT | ✅ | ipd-service (ADR-0017) |
| RPM vitals with danger bands | ✅ | notification-service vitals_rules |
| Questionnaires / structured intake | ✅ | fhir questionnaires + portal rail |
| Telehealth consult thread | ✅ | portal (ADR-0016) |
| Referrals with priority queues | ✅ | sync tier (ADR-0011) |

### Money
| Capability | Status | Where |
|---|---|---|
| Eligibility / enrolment | ✅ | claims-orchestrator (ADR-0017) |
| Adjudication + tariffs | ✅ | packages/adjudicator |
| Anti-fraud (linkage gate, biometrics, disputes, scorecard) | ✅ | ADR-0018 |
| Ledger (double-entry) | ✅ | services/ledger (Rust) |
| ERP (GL, stock, procurement) | ✅ | ERPNext bridge (ADR-0012) |
| PSP payment rails | ✅ | packages/payrails |

### Emergency (this wave)
| Capability | Status | Where |
|---|---|---|
| One-number SOS (WhatsApp, app, USSD gateway) | ✅ | dispatch-service (ADR-0019) |
| Triage, dedupe, facility routing | ✅ | dispatch-service |
| Case management + CRM | ✅ | dispatch-service (ADR-0019) |
| **Ambulance fleet + live GPS + ETA** | 🆕 | fleet.go — units, pings, honest ETA |
| **Uber-like live map (patient + facility)** | 🆕 | apps/ambulance-map.js, patient/track.html, pwa/dispatch.html |

### Intelligence
| Capability | Status | Where |
|---|---|---|
| Syndromic surveillance radar | ✅ | packages/surveillance (ADR-0013) |
| IDSR notifiable-disease case detection | ✅ | notifiable.py (ADR-0020) |
| Executable quality measures | ✅ | packages/quality (ADR-0020) |
| **DHIS2 national reporting export** | 🆕 | packages/dhis2 — MeasureReports + surveillance → dataValueSets, spooled |
| Analytics rollups (LGA/state) | ✅ | packages/analytics |
| ML fraud / GNN with drift monitoring | ✅ | ml/ (ADR-0014) |

### Workforce
| Capability | Status | Where |
|---|---|---|
| 14-role catalogue + onboarding chains | ✅ | docs/roles.md, registry /onboarding (ADR-0020) |
| Credential verification (MDCN/NMCN/PCN/MLSCN) | ✅ | tools/provcred |
| Staff rota / shift scheduling | 🗺️ | scheduling engine covers appointments; staff rota reuses the same slots+resources model — candidate for a small service when a state asks |
| HR/payroll | 🗺️ | deliberately composed, not built: ERPNext HR module is the designated peer (ADR-0005) |

### Remaining honest gaps (roadmap)
- **Blood bank inventory** — unit-level tracking with cold-chain ties;
  packages/coldchain covers temperature; blood-specific typing/expiry
  ledger is a roadmap package.
- **Equipment management (CMMS)** — oxygen plants, generators, cold-chain
  fridges as maintained assets; ERPNext Assets composes here.
- **Voice/IVR channel** — dispatch is WhatsApp/SMS/USSD/app; voice menus
  need a telecom partner integration (adapter seam, not a build).
- **Disaster/mass-casualty mode** — dispatch triage scales to one
  caller; an MCI mode (multi-casualty tagging, hospital bed surge view
  combining ipd-service + dispatch) is the next dispatch iteration.
- **DHIS2 live sync** — the exporter produces standards-correct
  payloads; the transport (direct API vs monthly spool) is per-state
  deployment policy, not code.

## Doctrine check

Nothing above embeds restricted-licence software: fleet tracking, the
map, the exporter are stdlib/canvas clean-room builds; ERP breadth
(HR, assets) is composed from ERPNext over REST (ADR-0005), never
forked.
