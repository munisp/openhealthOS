# Production-readiness scorecard

Date: 2026-10-18 · Scope: every platform feature's business rules, business
logic accuracy and completeness, plus the named infrastructure components.
Scores are engineering judgements against the platform's own go-live gates,
not marketing. **Nothing here is scored on intent — only on what is
implemented, tested, and provable in this repository today.**

Scale: **A** = production-ready, evidence in CI · **B** = production-ready
with stated operational prerequisites · **C** = functional core, named
gaps · **D** = partial/scaffold · **F** = absent or dishonest.

## Feature domains

| # | Domain | Score | Evidence | Known gaps (honest) |
|---|--------|-------|----------|---------------------|
| 1 | Identity & KYC | **A−** | ADR-0012: 4-level KYC ladder, hashed OTP (10-min TTL, 5-attempt lockout, one-time use, persisted), verifier seam (offline/HTTP/sandbox), deterministic dedupe gates → 409, fraud signals view; Go + PG tests | NIN verification needs a licensed NIMC integration (HTTP seam ready, credentials are a deployment concern); biometrics deliberately out of scope |
| 2 | Clinical record (FHIR) | **A−** | Medplum designated production server, 4 profiles + examples CI-validated against a live server; shr-service edge seam; OpenMRS/Bahmni FHIR2 bridge | Full FHIR R4 surface is Medplum's, not ours — by design (compose, don't fork) |
| 3 | Clinical loop (telehealth→pharmacy) | **A** | `demo/clinical-loop.sh` end-to-end; allergy gate structural; async store-and-forward telehealth (no bandwidth floor) | Video consults deliberately excluded (store-and-forward doctrine) |
| 4 | Claims adjudication | **A** | 1M-claim benchmark with zero invariant violations (~41k/s); payer router (NHIA schemes + HMOs), unknown-scheme refusal, cross-payer replay structurally impossible, per-payer idempotency | Tariff tables are fixtures pending state uploads |
| 5 | Payments & ledger | **A−** | Rust ledger: fsync journal, exact recovery across restarts, idempotent prepare/commit per txid, wallet idempotency migration 0006 | TigerBeetle evaluation documented but stdlib/Rust ledger shipped instead — see infrastructure table |
| 6 | Fraud detection | **B+** | ADR-0014: real PyTorch MLP (holdout AUC 0.823) + GCN (0.994 on ring structure), shipped champion weights, CPU inference, registry, drift PSI, A/B seam, frauddetect ML bridge; advisory-only doctrine | Trained on calibrated synthetic data — needs adjudicated production claims to validate beyond bootstrap (pipeline ready; stated in ADR-0014) |
| 7 | Geospatial | **B+** | ADR-0014: capacity profile + migration 0007, haversine `/facilities/near` (PG + memory parity, 400s on bad input), offline canvas map over bundled NE boundary, near-me in dashboards + patient app (5 languages), `geo:` navigation handoff | Registry capacity data must be populated by deployments (map is only as honest as the registry); no batch geospatial analytics yet (Sedona seam documented) |
| 8 | Sync (edge→LGA→state) | **A** | `demo/national-sync.sh`: priority queues, JSONL spill fsynced before ack, restart-from-spill, idempotent ingest; syncbench tool | — |
| 9 | Notifications & patient engagement | **A−** | persisted-attempt delivery, five-language rendering with graceful sparse-payload fallbacks, FHIR rest-hook intake CI-gated on real Medplum, USSD feed, patient PWA | SMS delivery depends on provider webhook credentials (deployment concern) |
| 10 | ERP (ERPNext bridge) | **B+** | mappers (stock/invoice/PO/journal with balance re-assertion), offline JSONL spool, `openhealth_id` idempotency, graceful degradation; GPL boundary respected (REST composition, never embedded) | Requires an ERPNext instance per finance office; chart-of-accounts mapping is configuration, not code |
| 11 | Security baseline | **A−** | stdlib OIDC (RS256/JWKS), `AUTH_REQUIRED=1` fail-closed, AES-256-GCM edge sealing (`SX1:`/`SXW1:`), hardened k8s (restricted PSS, seccomp, RO rootfs, default-deny NetworkPolicies), secret-scan in CI | Keycloak realm as code; rotating KMS is operational |
| 12 | Conformance & gates | **A** | OpenHIE-pattern conformance 9/9, Medplum gate 11/11, WCAG AA contrast checks (3 themes), licence hygiene, app self-containment — all in CI | — |
| 13 | Analytics & reporting | **B+** | pilot gates measured from raw events (dashboard mirrors `packages/analytics`), DHIS2 bridge, payer dashboard | National dashboards need production event volume to be meaningful |
| 14 | Innovation layer (ADR-0013) | **B+** | IPS QR (ed25519-signed), syndromic surveillance, approval-gated edge AI, provider VCs, household catchment, payrails, cold-chain prediction — all tested | edgeai models are extractive/std-lib by design; generative copilots remain external |

## Named infrastructure components — what we actually do with each

The honest answer to "how robust and integrated is X": some are composed
upstream components, some are deliberately re-implemented patterns (licence
or hot-path doctrine), and one is rejected. The distinction is the
platform's licence doctrine ([LICENSING.md](../LICENSING.md), ADR-0005):
BSL/SSPL/Elastic-licensed software is **never embedded** — its pattern is
re-implemented clean-room where the capability is needed.

| Component | Status | Score | What ships |
|-----------|--------|-------|------------|
| **Postgres** | composed, production spine | **A** | Every state-plane service has a PG store behind the same interface as its in-memory store; 7 idempotent migrations; K8s migration job; store-level parity tests |
| **TigerBeetle** | pattern re-implemented, binary not embedded | **B+** | BSL — never embedded. The Rust ledger implements the same doctrine (double-entry, two-phase transfers, fsync journal, exact recovery, idempotent txids). Gap vs TigerBeetle: no distributed consensus replication — single-writer Postgres-backed durability today; multi-writer consensus is the documented scale-out step |
| **Redis** | composed (Medplum only) | **B** | Ships as Medplum's cache in the `medplum` compose profile. Platform services are deliberately Redis-free (in-memory + PG); if distributed caching becomes necessary, Valkey (BSD) is the designated fork-compatible choice |
| **Mojaloop** | pattern, not embedded | **B+** | Apache-2.0 but a full Mojaloop deployment is its own programme. The wallet/switch seam implements the Mojaloop pattern (quotes, transfers, fulfilment semantics, per-scheme idempotency) so an interop switch can be adopted without changing wallets. Not a live Mojaloop hub — stated honestly |
| **Kafka** | pattern re-implemented for the edge | **B+** | The event spine (envelope schema in `shared/eventschema`, versioned topics, idempotent consumers, replay tooling in `packages/replay`) is Kafka-shaped; the edge runs JSONL spill + sync-ingest because Kafka cannot run at a PHC. State plane can adopt real Kafka/Pulsar at the documented seam without consumer changes |
| **APISIX** | pattern in stdlib Go gateway | **B+** | `services/api-gateway` implements the gateway doctrine (route table, authn/z, rate-limits, bounded timeouts, graceful drain) with zero dependencies. APISIX (Apache-2.0) can front the same services at national scale; the seam is the route config, not the code |
| **Keycloak** | composed, realm as code | **A−** | Realm checked in; stdlib OIDC verification (RS256/JWKS) in every service — services boot and verify with any compliant issuer, edge keeps running with the IdP down; `AUTH_REQUIRED=1` fails closed |
| **OpenAppSec / WAF** | equivalent controls in-platform | **B** | Not embedded. Controls instead: default-deny NetworkPolicies, restricted PodSecurity + seccomp + RO rootfs, request size/timeouts in `shared/servex`, authn/z fail-closed, secret-scan. A WAF at the ingress is an ops add-on, not an app dependency |
| **Permify** | pattern in `shared/authz` | **B+** | Relationship-based authorization doctrine (ReBAC: facility↔staff↔patient grants) implemented stdlib-only; Permify itself (AGPL, embeddable) remains an adoption option behind the same checker interface |
| **OpenSearch** | rejected — licence | **C** | SSPL/Elastic-adjacent licensing history; never embedded. Designated alternative: search via Postgres (tsvector/pg_trgm covers registry + record search at state scale) today; if a dedicated search tier is needed, Apache-licensed options (Lucene directly, or OpenSearch only after its licence posture is re-verified) are the seam |
| **Fluvio** | not adopted | **C** | Streaming niche already covered by the event spine + sync-ingest (see Kafka row). Adding a second streaming layer would be redundancy, not robustness |
| **Neo4j** | pattern re-implemented (GNN), not embedded | **B+** | Neo4j's enterprise/clustering features are restrictive-licensed. The fraud graph lives as the facility↔member bipartite claim graph in `ml/openhealth_ml` (hand-rolled GCN, no graph DB required) and as relational edges in Postgres. A graph database is genuinely useful for fraud-ring exploration UI — documented as a future Apache-licensed option (e.g. Apache AGE on the existing Postgres) |

## Method note

Every A/B score above names its evidence: a test suite, a demo script, a
benchmark, or a CI gate — all runnable from this repository. Where the
evidence is synthetic (ML bootstrap champions), that is stated in the same
breath as the score. That is the difference between a readiness score and a
marketing claim.
