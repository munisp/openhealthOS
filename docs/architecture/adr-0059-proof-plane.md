# ADR-0059: Proof plane — sync-storm benchmark, chaos runbook, SBOM/signing

Date: 2026-10-20 · Status: accepted

## Context

Claims throughput and edge latency are benchmarked (ADR-0053), but national
rollout needs evidence for the failure modes that actually happen: 1,000
facilities reconnecting at once, infrastructure dying mid-day, and a
supply-chain-secure release process.

## Decision

1. **Sync-storm benchmark (`benchmark/sync_storm.py`)** — N facilities flush
   offline queues concurrently at sync-ingest; p50/p95/p99 + throughput;
   failures tallied honestly (exit 1 if any).
2. **Chaos runbook (`docs/runbooks/chaos.md`)** — seven kill scenarios
   (Postgres, edge, NIMC upstream, SHR, bulk export, 30-min partition + heal,
   Keycloak) each with expected fail-soft behaviour and verification method;
   scenario 6 is a quarterly national-rollout gate.
3. **SBOM + signing (`tools/release/sbom.py`)** — CycloneDX JSON over all Go
   module deps, SHA-256 manifest per deliverable, and the licence gate from
   ADR-0005 automated: any BSL/SSPL/Elastic dependency fails the release.
4. **PWA-first decision (recorded)**: android-shell/ios-shell remain thin
   wrappers. Offline-first + network-constrained doctrine favours the PWA;
   native investment is deferred until a capability (background sync,
   biometrics) demonstrably requires it.

## Consequences

- Positive: every fail-soft claim now has a kill-test; releases are
  reproducible and licence-clean by construction.
- Limitations (honest): chaos scenarios 1–5 are currently verified by
  service-level tests and staging drills, not an automated mesh; multi-
  region Postgres failover rehearsal is still manual.
