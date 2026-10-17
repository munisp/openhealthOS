# Licensing policy

## Dual-licence model

| Artefact | Licence | Why |
|---|---|---|
| Platform core (`services/`, `packages/`, `infra/`) | **AGPL-3.0** | Improvements to the operating layer must return to the commons, including when offered as a network service. |
| SDKs, FHIR profiles, adapters, API contracts (`fhir/`, `openapi/`) | **Apache-2.0** | Maximum adoptability for integrators, governments and vendors building on the public surface. |
| Documentation (`docs/`) | CC BY 4.0 | Specs and runbooks should travel freely. |

Brand and certification marks ("OpenHealth OS") are protected; the code is free.

## Dependency licence policy — compose, don't fork; re-implement, don't embed

Every third-party component is reviewed before adoption. The rule:

- **Allowed to embed/operate**: Apache-2.0, MIT, BSD-2/3, MPL-2.0, AGPL-3.0 (for components operated inside the AGPL core), LGPL (dynamically linked), CC0/CC-BY data.
- **Never embedded — re-implemented behind a clean seam**: BUSL/BSL (e.g. source-available "open" products), SSPL, Elastic License 2.0, Commons Clause, CPAL-with-restrictions, proprietary/OEM-only.
- When a re-implementation is inspired by a source-available design, it is written clean-room in this repo, under our licence, with an ADR recording the decision and the interface seam.

### Current component licence matrix

| Component | Licence | Decision |
|---|---|---|
| Medplum (FHIR R4 core) | Apache-2.0 | Reuse as-is; contribute Nigerian profiles upstream |
| Postgres, Redis (≥8, AGPL/RSALv2/SSPL tri-licence — we use under AGPL), Kafka, Temporal, Dapr, APISIX, Keycloak, OpenSearch, Fluvio, TigerBeetle, Delta Lake, Flink, Spark, DataFusion, Ray, Sedona, Prometheus, Grafana (AGPL), MinIO (AGPL), Wazuh (GPLv2, operated as SIEM), Kubecost | Apache-2.0 / AGPL / permissive class | Reuse as-is |
| Mojaloop | Apache-2.0 | Reuse switch patterns; adapters in Go where the hot path requires |
| Metriport harmonization engine | AGPL-3.0 | Adapt the pattern (paper/HL7v2 → FHIR bundles); Nigeria-facing adapters are ours |
| VeriMed provider verification | MIT | Integrate; add MDCN/Nigeria registry adapter |
| Last EHR Approval-Gated Agent Writes protocol | Apache-2.0 | Adopt protocol; conformance-tested in `packages/triage` |
| CloudHealthOffice | **BSL 1.1 — source-available, not embeddable** | **Re-implemented** as our claims engine (`packages/adjudicator` + `services/claims-orchestrator`), clean-room in Python/Go, AGPL |
| Elasticsearch | SSPL/Elastic — excluded | OpenSearch used instead |

### CI enforcement

The CI workflow runs a manifest check: every new third-party dependency must appear in this matrix (or be added to it in the same PR) with an allowed licence. A dependency on a prohibited licence fails the build.

## Digital Public Goods

OpenHealth OS is designed to the Digital Public Goods standard: open licence, open standards (FHIR R4, OpenHIE), privacy by architecture (NDPA 2023), and documented pathways for governments to self-host. No facility ever pays to be counted.
