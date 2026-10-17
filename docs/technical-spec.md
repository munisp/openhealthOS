# Technical & architectural specification

*Condensed from Specification v1.0 §5. The five commitments: offline-first, standards-native, federated, frugal, secure by default.*

## Layered architecture

```
L5 · Applications (open surface)   EMR · CHW app · claims console · analytics · 3rd-party apps
L4 · Interop & exchange            FHIR R4 API gateway · consent manager · terminology · SHR
                                   · DHIS2/NHIA/NIMC bridges
L3 · National cloud core (K8s)     Postgres HA · FHIR server · Kafka events · MinIO · Keycloak · GitOps
L2 · Connectivity                  2G/3G/4G · Starlink/VSAT for high-value sites
                                   · store-and-forward sync · USSD/SMS fallback
L1 · Facility edge (offline-first) $1,200 edge node (mini-PC, 1TB cache, local Wi-Fi/LAN)
                                   · solar + LFP battery (15W avg) · IoT telemetry · PWA clients
```

State data planes hang off L3 via the same contracts. Any layer can degrade without taking the stack down.

Cross-cutting: identity & RBAC, NDPA compliance, audit trail, observability, open source, and edge telemetry (solar SOC, battery health, water level, cold-chain temp, equipment tickets).

## The offline-first edge

| Element | Specification |
|---|---|
| Edge node (per PHC cluster) | Mini-PC (4-core, 16GB, 1TB NVMe): FHIR store replica, terminology cache, sync agent, local Wi-Fi/LAN; ~15W average draw |
| Power | Solar + 300Wh LFP battery, 48h autonomy; charge-controller telemetry streams to the infrastructure module |
| Clients | PWAs on any Android; local-first DB with CRDT-based sync (last-writer-wins + semantic merge for clinical facts); conflict UI for clinicians |
| Sync | Store-and-forward over HTTPS; payloads compressed <1MB/facility-day; SMS/USSD for eligibility, enrolment, alerts |
| Resilience | 30+ days fully offline; edge self-heals and re-replicates; cold-chain and power alerts queue via SMS |

## Data & interoperability

- FHIR R4 profiles for Patient, Encounter, Observation, Medication, Claim, Coverage, Consent — published as an open implementation guide (`fhir/`).
- Consent manager: every cross-facility share requires a signed, revocable consent artefact (HIE-CM pattern).
- Identity linkage: NIN binding via NIMC where available; progressive enrolment otherwise.
- Claims exchange: NHIA/state-scheme adapters to a national claims-gateway contract (`openapi/claims-gateway.yaml`); every claim carries pre-auth and adjudication status.
- Aggregate reporting: automatic DHIS2 bridge (`packages/dhis2-bridge`).

## Security & compliance

NDPA 2023 baseline: data localisation per state, field-level RBAC, encryption in transit/at rest, immutable audit logs, patient-facing consent receipts. ISO 27001 path within 24 months; annual pen tests; signed OTA images to edge nodes.

## Platform stack blueprint

| Plane | Components |
|---|---|
| Edge | Go sync agent · Rust telemetry collectors · SQLite/CRDT · SMS/USSD fallback · signed OTA images |
| Access | APISIX gateway · openappsec ML-WAF · Keycloak OIDC · FHIR routing · rate limits · consent enforcement |
| Core (K8s, multi-AZ, per-state planes) | Medplum FHIR server · Dapr sidecars · Temporal durable workflows · Kafka · Fluvio · Postgres · Redis · TigerBeetle ledger |
| Value services | Claims engine (Go/Rust/Python) · eligibility · marketplace · payment rails (Mojaloop patterns) · provider verification |
| Data / lakehouse | Parquet + Delta Lake (bronze/silver/gold) on MinIO · Flink CDC · Spark batch · DataFusion · Ray ML · Sedona geospatial |
| Security & ops | Wazuh SIEM/XDR · OpenCTI · OpenSearch · Kubecost cost guardrails · OTel tracing |

## Non-functional targets

| Requirement | Target |
|---|---|
| Clinical workflow latency (offline) | <200ms local; zero hard network dependency |
| Sync SLA | reconciled <15 min after connectivity; <1MB/facility/day |
| Claims throughput | >100 claims/sec sustained; zero unaccounted outcomes |
| Ledger integrity | zero balance drift — double-entry on every movement |
| Core uptime | 99.5% (claims/eligibility degrade to offline queue) |
| Scale headroom | 40,000 facilities; 10M+ FHIR resources per state plane |
| Edge cost | ≤$1,200 capex per PHC; ≤$2.5/facility/month |

## Seamless-integration guarantees

1. Every cross-component contract is exercised by CI conformance suites on every merge.
2. Temporal deterministic replay re-derives any past claim decision from the same events.
3. Dapr abstraction lets a state plane swap storage or transport without touching domain code.
4. Kubecost-enforced budgets make per-state hosting self-documenting.
5. All edge fleets run signed, staged images; Wazuh watches them; rollback is one command.
