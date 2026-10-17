# OpenHealth OS

**An open-source operating system for healthcare in developing countries — designed for unreliable power, scarce water, constrained networks, and 34,076 primary health centres.**

OpenHealth OS is digital public infrastructure: a free, open-source, offline-first platform that makes primary health centres, insured lives, and out-of-pocket health spending legible to each other. Nigeria is the first pilot; the design is African-country scale.

- **Product**: offline-first healthcare OS · FHIR R4 · OpenHIE-aligned · twelve modules in four groups
- **Pilot**: one Nigerian state, 50 → 200 facilities, six-month measured gates
- **Designed-in constraints**: grid collapse, 22% of hospitals with clean running water, 50–80% of medical equipment non-functional, 0.18 doctors per 1,000 people
- **Licences**: AGPL-3.0 (platform core) · Apache-2.0 (SDKs, FHIR profiles, adapters) — see [LICENSING.md](LICENSING.md)

## The twelve modules

| # | Module | Where it lives |
|---|--------|----------------|
| 1 | Identity & registries (patient / provider / facility / terminology) | [`services/registry-service`](services/registry-service) — NIN binding + offline-resolvable patient IDs |
| 2 | Core health record (FHIR R4 SHR, consent artefacts) | [`fhir/`](fhir) profiles + [`services/consent-service`](services/consent-service) (HIE-CM pattern) + Medplum-compatible core |
| 3 | Community health (CHW tasks, USSD fallback) | [`services/ussd-gateway`](services/ussd-gateway) — feature-phone eligibility & enrolment |
| 4 | Claims & insurance rails (eligibility, pre-auth, adjudication) | [`services/claims-orchestrator`](services/claims-orchestrator) + [`packages/adjudicator`](packages/adjudicator) |
| 5 | Payments (wallets, instant payment, reconciliation) | [`services/wallet-service`](services/wallet-service) (micro-collections, payouts) + [`services/ledger`](services/ledger) + Mojaloop-pattern switch seam |
| 6 | Procurement marketplace | [`packages/procurement`](packages/procurement) — pooled ordering + supplier scorecards from stock-out telemetry |
| 7 | Laboratory (OpenELIS-based LIMS) | upstream OpenELIS, results as FHIR Observations |
| 8 | Telehealth + AI triage | [`packages/triage`](packages/triage) — approval-gated writes only |
| 9 | **Infrastructure layer (the differentiator)** — power, water, cold chain, equipment | [`services/telemetry-collector`](services/telemetry-collector) + [`services/equipment-service`](services/equipment-service) (BMET ticket routing, donation screening) |
| 10 | Analytics & developer platform | [`packages/analytics`](packages/analytics) (pilot-gate metrics) + [`packages/dhis2-bridge`](packages/dhis2-bridge) + public FHIR APIs |

Edge-to-core sync: [`services/sync-agent`](services/sync-agent) (edge) ↔ [`services/sync-ingest`](services/sync-ingest) (state plane, idempotent).

## Architecture in one picture

```
L5  Applications        EMR · CHW app · claims console · analytics · 3rd-party apps
L4  Interop & exchange  FHIR R4 gateway · consent manager · terminology · DHIS2/NHIA/NIMC bridges
L3  Cloud core (K8s)    Postgres HA · FHIR server · Kafka events · MinIO · Keycloak OIDC · GitOps
L2  Connectivity        2G/3G/4G · store-and-forward sync · USSD/SMS fallback
L1  Facility edge       $1,200 edge node · solar + LFP battery (15W avg) · IoT telemetry · PWA clients
```

Any layer can degrade without taking the stack down. Full detail: [docs/technical-spec.md](docs/technical-spec.md).

## Three-language doctrine

| Language | Workloads | In this repo |
|----------|-----------|--------------|
| **Go** | high-throughput services, sync agents, gateway plugins | claims orchestrator, registry service, sync agent, mock switch |
| **Rust** | the ledger, telemetry collectors, hot data paths | double-entry ledger, telemetry collector |
| **Python** | adjudication rules, analytics, ML, data pipelines | adjudicator, triage engine, DHIS2 bridge |

## Quickstart — the claims money path, end to end

The claims pipeline (claim → adjudication → double-entry ledger → Mojaloop-pattern settlement) runs locally with three processes:

```bash
./demo/demo.sh
```

Or run the pieces individually (Go 1.22+, Rust stable, Python 3.11+; stdlib-only, zero third-party dependencies):

```bash
# unit tests, per language (Go modules are independent — run per module)
for m in claims-orchestrator payment-switch-mock registry-service sync-agent \
         sync-ingest consent-service ussd-gateway wallet-service equipment-service; do
  (cd "services/$m" && go test ./...)
done
cargo test --manifest-path services/ledger/Cargo.toml
cargo test --manifest-path services/telemetry-collector/Cargo.toml
for p in adjudicator triage dhis2-bridge procurement analytics; do
  python3 -m unittest discover -s "packages/$p"
done
```

## Repository layout

```
services/    Go + Rust deployable services (the runtime plane)
packages/    Python domain packages (rules, analytics, ML seams)
fhir/        FHIR R4 implementation guide — Nigerian profiles (Apache-2.0)
openapi/     Claims-gateway contract (NHCX-analogue): eligibility, pre-auth, claims, status
infra/       docker-compose dev plane · Kubernetes state-plane skeleton
docs/        product / technical / business specs, ADRs, runbooks
demo/        end-to-end claims demo script
examples/    sample claim payloads
```

## Design doctrine

1. **Offline-first is a right, not a feature** — every clinical, enrolment and claims workflow runs with zero connectivity; connectivity is an optimisation, never a dependency.
2. **Compose, don't fork** — we reuse upstream open-source components unchanged (Medplum, Kafka, Temporal, Keycloak, Postgres…) and re-implement only where a licence or the hot path demands it. See [LICENSING.md](LICENSING.md) and [ADR-0005](docs/architecture/adr-0005-license-policy-reuse-vs-reimplement.md).
3. **Money rails are health infrastructure** — claims, pre-authorisation, payments and procurement are native, auditable events, not PDFs. The ledger is double-entry; balances are never hand-rolled tables.
4. **AI never writes unreviewed** — every agent/model write into the record follows the Approval-Gated Agent Writes protocol: *Proposal → Decision → Commit → Audit* ([ADR-0004](docs/architecture/adr-0004-approval-gated-agent-writes.md)).
5. **The physical layer is in scope** — power, water, cold chain and equipment telemetry are first-class data; the platform knows which PHC is dark, dry, or hot before a vaccine dies.

## Non-functional targets

| Requirement | Target |
|---|---|
| Clinical workflow latency (offline) | <200 ms local; zero hard dependency on network |
| Sync SLA | reconciled <15 min after connectivity; <1 MB/facility/day |
| Claims throughput | >100 claims/sec sustained, zero unaccounted outcomes |
| Ledger integrity | zero balance drift — double-entry on every money movement |
| Core uptime | 99.5% (claims & eligibility degrade gracefully to offline queue) |
| Edge cost | ≤$1,200 capex per PHC; ≤$2.5/facility/month connectivity+cloud |

## Contributing

OpenHealth OS is built in the open and upstreams improvements to the commons it composes (OpenMRS, OpenELIS, DHIS2, Medplum, OpenHIE). See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
