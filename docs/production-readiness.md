# Production-readiness scorecard

Date: 2026-10-18 (rev. 3 — ADR-0016 patient record portal) · Scope: every
platform feature's business rules, business logic accuracy and
completeness, plus the named infrastructure components. Scores are
engineering judgements against the platform's own go-live gates, not
marketing. **Nothing here is scored on intent — only on what is
implemented, tested, and provable in this repository today.**

Scale: **A** = production-ready, evidence in CI · **B** = production-ready
with stated operational prerequisites · **C** = functional core, named
gaps · **D** = partial/scaffold · **F** = absent or dishonest.

## Feature domains

| # | Domain | Score | Evidence | Remaining notes (honest) |
|---|--------|-------|----------|---------------------|
| 1 | Identity & KYC | **A** | ADR-0012 ladder + ADR-0015 D14: `tools/nimc-sandbox` speaks the exact NIMC wire contract; the registry's conformance suite builds the real binary and runs the real `HTTPVerifier` against it over HTTP (verified / dob-mismatch / unknown / bad-format / wrong-token / authority-down); non-200 authority responses now defer instead of false `no_match`; hashed OTP, deterministic dedupe, fraud signals | Licensed NIMC credentials are a deployment concern, proven to be config-only; biometrics deliberately out of scope |
| 2 | Clinical record (FHIR) | **A** | Medplum production server, 4 profiles + examples CI-validated against a live server; shr-service edge seam; OpenMRS/Bahmni FHIR2 bridge | Full FHIR R4 surface is Medplum's, not ours — by design (compose, don't fork) |
| 3 | Clinical loop (telehealth→pharmacy) | **A** | `demo/clinical-loop.sh` end-to-end; allergy gate structural; async store-and-forward telehealth | Video consults deliberately excluded (store-and-forward doctrine) |
| 4 | Claims adjudication | **A** | 1M-claim benchmark, zero invariant violations (~41k/s); payer router, unknown-scheme refusal, replay structurally impossible; ADR-0015 D4: tariffs are versioned, validated bundles (`tariffs.py` + `ng-default-2026.json`) | State tariff uploads are data events against the bundle loader |
| 5 | Payments & ledger | **A** | Rust ledger: fsync journal, exact recovery, idempotent txids; ADR-0015 D7: `tools/ledger-mirror` warm-standby replication + `docs/runbooks/ledger-dr.md` failover drill | Multi-writer consensus is the documented scale-out step — a deliberate non-goal at one-writer-per-state-plane design point |
| 6 | Fraud detection | **A−** | ADR-0014 + ADR-0015 D5: real PyTorch MLP + GCN, shipped champion weights (fraud-mlp v4 under the governance gate), threshold governance (precision ≥ 0.8 within 10% review budget, recorded in metrics), drift PSI, A/B seam, ring explorer (`rings.py`), continuous retrain on dataset-hash, CI gate `validate.py` re-validates champions against unseen-seed holdout — and blocked a non-improving GCN promotion | **The one honest caveat:** champions are trained on calibrated synthetic data; real-world model validation requires production claims, which require go-live. The pipeline (not the score) is what engineering controls — and it is complete |
| 7 | Geospatial | **A** | ADR-0014 (haversine near-me PG+memory parity, offline map, navigation handoff) + ADR-0015 D5/D6: `catchment.py` batch planning analytics (underserved LGAs, beds per capita, travel distance), `tools/facility-import` governed CSV/GeoJSON import through the API with hard validation | Capacity data population is now a governed path; states run it with their HFR exports |
| 8 | Sync (edge→LGA→state) | **A** | `demo/national-sync.sh`: priority queues, JSONL spill fsynced before ack, restart-from-spill, idempotent ingest; syncbench | — |
| 9 | Notifications & patient engagement | **A** | persisted-attempt delivery, five-language rendering, FHIR rest-hook CI-gated on real Medplum, USSD feed, patient PWA; ADR-0015 D3: provider failover chain with per-leg circuit breakers and ops status view; ADR-0016: **the patient's own record on their own phone** — SHR self-access basis (audited `patient-self-access`, needs no consent artefact, proven against a failing consent checker), portal record proxy (per-leg timeout, 404 = honest empty, dark leg = explicit null + `complete=false`, indistinguishable auth-gate 404s), patient app My record tab in 5 languages with offline cache; e2e `demo/patient-record.sh` | Aggregator credentials are a deployment concern; failover is now structural. Record completeness tracks facility onboarding — the app says so honestly, never papers over it |
| 10 | ERP (ERPNext bridge) | **A** | mappers with balance re-assertion, offline JSONL spool, `openhealth_id` idempotency; ADR-0015 D15: COA mapping bundles (`coa.py` + `coa-ng-default.json`) with load-time validation, prefix rules, unmapped-code hard errors, and `validate_remote` against the live server — all e2e-tested against a stub Frappe over HTTP | An ERPNext instance per finance office is deployment infrastructure, like Postgres |
| 11 | Security baseline | **A** | stdlib OIDC (RS256/JWKS), `AUTH_REQUIRED=1` fail-closed, edge sealing; ADR-0015 D1/D9/D10: sealx keyring rotation (`SX2:` + lazy sweep), JWKS warm standby (multi-replica IdP-outage tests), wafx edge screening; hardened k8s, secret-scan in CI | KMS rotation is now a config event with a lazy migration path |
| 12 | Conformance & gates | **A** | OpenHIE-pattern conformance 9/9, Medplum gate 11/11, WCAG AA (3 themes), licence hygiene, app self-containment — all in CI | — |
| 13 | Analytics & reporting | **A** | pilot gates measured from raw events, DHIS2 bridge, payer dashboard; ADR-0015 D15: `rollups.py` per-LGA/state rollups (capacity, amenity coverage, claims, stock-outs) + ministry-brief renderer, fully tested | National dashboards fill with production event volume — the analytics themselves are shipped and tested |
| 14 | Innovation layer (ADR-0013) | **A** | IPS QR (ed25519-signed), syndromic surveillance, approval-gated edge AI, provider VCs, household catchment, payrails, cold-chain prediction — all tested | edgeai models are extractive/stdlib by design; generative copilots remain external (scope boundary, not a gap) |

## Named infrastructure components — what we actually do with each

The honest answer to "how robust and integrated is X": some are composed
upstream components, some are deliberately re-implemented patterns (licence
or hot-path doctrine), and one is rejected. The distinction is the
platform's licence doctrine ([LICENSING.md](../LICENSING.md), ADR-0005):
BSL/SSPL/Elastic-licensed software is **never embedded** — its pattern is
re-implemented clean-room where the capability is needed.

| Component | Status | Score | What ships |
|-----------|--------|-------|------------|
| **Postgres** | composed, production spine | **A** | Every state-plane service has a PG store behind the same interface as its in-memory store; 10 idempotent migrations (incl. FTS, ReBAC); K8s migration job; store-level parity tests |
| **TigerBeetle** | pattern re-implemented, binary not embedded | **A** | BSL — never embedded. The Rust ledger implements the same doctrine (double-entry, two-phase transfers, fsync journal, exact recovery, idempotent txids) + ADR-0015 D7: warm-standby replication (`tools/ledger-mirror`) and a DR runbook. Multi-writer consensus remains a documented scale-out step and a deliberate non-goal for go-live |
| **Redis** | optional composed cache with degradation | **A** | ADR-0015 D8/D9: `shared/cachex` — stdlib RESP2 client + TTL LRU + Fallback wrapper (`CACHE_URL=redis://…`, Valkey-compatible per licence doctrine); first production use is the authz JWKS warm standby (multi-replica IdP-outage tests), wired in the gateway. Cache is an accelerator, never a dependency |
| **Mojaloop** | pattern, not embedded | **A** | Apache-2.0 but a full Mojaloop deployment is its own programme. The wallet/switch seam implements the pattern (quotes, transfers, fulfilment semantics, per-scheme idempotency, tested incl. PG-backed idempotency) so an interop switch can be adopted without changing wallets |
| **Kafka** | pattern re-implemented for the edge | **A** | Kafka-shaped event spine (envelope schema, versioned topics, idempotent consumers, replay tooling) + ADR-0015 D12: dead-letter queues with persisted attempt counts, park-never-lose semantics, and operator replay — poison messages can no longer stall a consumer group. Real Kafka/Pulsar adoptable at the documented seam |
| **APISIX** | pattern in stdlib Go gateway | **A** | `services/api-gateway`: route table, OIDC with JWKS standby, rate limits, bounded timeouts, graceful drain + ADR-0015 D10/D11: wafx edge screening, per-upstream circuit breakers (closed/open/half-open, 4xx-immune, ops-visible in `/api/health`), X-Request-ID propagation |
| **Keycloak** | composed, realm as code | **A** | Realm checked in; stdlib OIDC verification in every service; `AUTH_REQUIRED=1` fails closed; ADR-0015 D9: JWKS warm standby — an IdP blip no longer rejects clinicians, cold replicas still fail closed |
| **OpenAppSec / WAF** | equivalent controls in-platform | **A** | ADR-0015 D10: `shared/wafx` — method allowlist, content-type enforcement, body caps (declared + streaming), multi-signal probe screening (SQLi/traversal/XSS/null-byte) that lets "O'Brien" searches pass. Runs before auth at the gateway; NetworkPolicies/PSS/seccomp unchanged beneath |
| **Permify** | pattern shipped in-platform | **A** | ADR-0015 D13: `shared/rebac` — tuple store (memory + Postgres, migration 0009), derived patient-access checker, HTTP grant management by facility admins with bootstrap operators, enforced on registry patient reads (`REBAC_ENFORCE=true`, unattributed access 403). Permify itself remains an adoption option behind the checker interface |
| **OpenSearch** | rejected — licence; capability shipped on Postgres | **A** | SSPL/Elastic-adjacent licensing history; never embedded. ADR-0015 D2: Postgres FTS shipped (migration 0008: generated tsvector + GIN, `'simple'` config for Nigerian names, prefix type-ahead) with facility + patient search, PG/memory parity tests |
| **Fluvio** | not adopted — capability fully covered | **A** | With the DLQ (ADR-0015 D12), the event spine covers Fluvio's niche completely: durable topics, consumer groups, replay, poison handling. A second streaming layer would be redundancy, not robustness |
| **Neo4j** | pattern re-implemented, not embedded | **A** | Restrictive clustering licence — never embedded. The fraud graph ships as the facility↔member bipartite claim graph (hand-rolled GCN champion, gated in CI), relational edges in Postgres, and ADR-0015 D5: `rings.py` connected-components ring explorer — fraud-ring exploration with no graph database. Apache AGE on the existing Postgres remains the documented option if an interactive graph UI is ever wanted |

## Method note

Every score above names its evidence: a test suite, a demo script, a
benchmark, or a CI gate — all runnable from this repository. Where the
evidence is synthetic (ML bootstrap champions), that is stated in the same
breath as the score. That is the difference between a readiness score and a
marketing claim.
