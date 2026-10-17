# ADR-0005: Licence policy — compose, don't fork; re-implement, don't embed

- Status: Accepted
- Date: 2026-10-17
- Spec reference: §5.8 doctrine, §5.10 decision map

## Context

The platform composes proven open-source infrastructure. Some adjacent projects whose architecture we admire are **source-available, not open source** (e.g. CloudHealthOffice under BSL 1.1). Embedding BSL/SSPL/Elastic-licensed code would contaminate the AGPL/Apache-2.0 licensing the project's government-adoption credibility depends on.

## Decision

1. **Reuse as-is** — Apache-2.0/MIT/BSD/AGPL-class components (Medplum, Kafka, Temporal, Dapr, APISIX, Keycloak, OpenSearch, Fluvio, Redis≥8 under AGPL, Postgres, TigerBeetle, Delta Lake, Flink, Spark, DataFusion, Ray, Sedona, Kubecost), tracked upstream with pinned versions.
2. **Adapt the pattern** — where a project's design is right but its scope is foreign (Metriport's US network coverage), we reuse the architecture and build Nigeria-facing adapters.
3. **Integrate** — permissively-licensed engines (VeriMed, MIT) get registry adapters (MDCN/Nigeria).
4. **Adopt protocol** — Last EHR's Approval-Gated Agent Writes (Apache-2.0) becomes standard for all AI writes (ADR-0004).
5. **Re-implement** — BSL/SSPL/Elastic-licensed designs are never embedded. The claims adjudication pipeline (inspired by CloudHealthOffice's adjudication/benefit/pricing architecture and its evidence-first benchmarking) is re-implemented clean-room in this repo: `packages/adjudicator` (Python rules engine) + `services/claims-orchestrator` (Go) + `services/ledger` (Rust), all AGPL/Apache-2.0.
6. **CI enforcement** — every new dependency must enter the licence matrix in [LICENSING.md](../../LICENSING.md) with an allowed licence in the same PR, or the build fails.

## Consequences

- The Nigerian Million-Claim Challenge (published, reproducible NHIA-scale benchmark with truthful scoring, >100 claims/sec) runs against our own engine — no licence ambiguity at the heart of the revenue model.
- States can self-host every byte of the stack; that is precisely why states adopt it.
