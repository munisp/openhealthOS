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
| 1 | Identity & registries (patient / provider / facility / terminology) | [`services/registry-service`](services/registry-service) — KYC ladder (self → phone-OTP → NIN-format → NIMC-verified) that never blocks care, deterministic dedupe gates (NIN / phone+DOB / name+DOB), offline-resolvable patient IDs, `/kyc/signals` fraud view |
| 2 | Core health record (FHIR R4 SHR, consent artefacts) | [`fhir/`](fhir) profiles + [`services/shr-service`](services/shr-service) (FHIR store, consent-enforced cross-facility reads) + [`services/consent-service`](services/consent-service) (HIE-CM pattern) |
| 3 | Community health (CHW tasks, USSD fallback) | [`packages/chw-tasks`](packages/chw-tasks) (ANC/NPI schedules, danger-sign referrals) + [`services/ussd-gateway`](services/ussd-gateway) — feature-phone eligibility & enrolment |
| 4 | Claims & insurance rails (eligibility, pre-auth, adjudication) | [`services/claims-orchestrator`](services/claims-orchestrator) + [`packages/adjudicator`](packages/adjudicator) — scheme-driven payer network (`payers.py`): NHIA schemes and commercial HMOs route by config, unknown schemes refused, cross-payer double-dipping structurally impossible |
| 5 | Payments (wallets, instant payment, reconciliation) | [`services/wallet-service`](services/wallet-service) (micro-collections, payouts) + [`services/ledger`](services/ledger) + Mojaloop-pattern switch seam |
| 6 | Procurement marketplace | [`packages/procurement`](packages/procurement) — pooled ordering + supplier scorecards from stock-out telemetry |
| 7 | Laboratory (hub-and-spoke LIS) | [`packages/lis-bridge`](packages/lis-bridge) — sample chain-of-custody, results as FHIR Observations pushed to the SHR, critical-value surfacing |
| 8 | Telehealth + AI triage | [`services/telehealth-service`](services/telehealth-service) (nurse-first **asynchronous store-and-forward** consults — no workflow requires a live session, so no bandwidth floor below which care stops; referral loop; every consult closes as a FHIR Encounter, every prescription as a FHIR MedicationRequest in the SHR **and** a routed pharmacy dispense-request) + [`packages/triage`](packages/triage) — approval-gated writes only |
| 8a | Pharmacy & dispensing | [`services/pharmacy-service`](services/pharmacy-service) — tracer-drug formulary with allergy classes, per-facility stock, atomic dispense = status flip + stock decrement + NHIA claim draft; the allergy gate is structural (blocked prescriptions are recorded, never dropped) |
| 9 | **Infrastructure layer (the differentiator)** — power, water, cold chain, equipment | [`services/telemetry-collector`](services/telemetry-collector) + [`services/equipment-service`](services/equipment-service) (BMET ticket routing, donation screening) |
| 11 | Patient engagement & notification rails | [`services/notification-service`](services/notification-service) — subscriptions, SMS provider seam (webhook, never a proprietary SDK), persisted-attempt delivery states, five-language rendering, no clinical detail over SMS |
| 10 | Analytics & developer platform | [`packages/analytics`](packages/analytics) (pilot-gate metrics) + [`packages/dhis2-bridge`](packages/dhis2-bridge) + public FHIR APIs |

Edge-to-core sync: [`services/sync-agent`](services/sync-agent) (edge) → [`services/lga-hub`](services/lga-hub) (LGA store-and-forward: priority queues — critical referrals overtake bulk telemetry — JSONL spill fsynced before ack, power-cut-proof, idempotent) → [`services/sync-ingest`](services/sync-ingest) (state plane, idempotent). See [ADR-0011](docs/architecture/adr-0011-national-scale.md).

FHIR foundation: **Medplum** (Apache-2.0) is the designated production FHIR server — profile-gated in [`infra/compose/platform.yaml`](infra/compose/platform.yaml) (`--profile medplum`) with its own Postgres + Redis; [`tools/medplum-validate`](tools/medplum-validate) proves the four OpenHealth profiles and examples against a live server. shr-service stays the stdlib edge/dev seam behind the same envelope contract. Hospitals running OpenMRS/Bahmni interoperate via [`packages/adapters/openmrs.py`](packages/adapters/openmrs.py) (FHIR2 bridge) — peers, never rewrites. See [ADR-0010](docs/architecture/adr-0010-clinical-loop-emr.md).

National-scale proof: `bash demo/national-sync.sh` — facility → LGA hub → state plane: priority forwarding order, duplicate retry dedupe, hub killed mid-flight and restarted from the spill with queues intact.

ERP composition: **ERPNext** (GPL-3.0) is the finance office's book — GL, warehouse stock, supplier orders — composed over its REST API, never embedded. [`packages/erpnext-bridge`](packages/erpnext-bridge) projects platform events (stock movements → Stock Entries, approved claims → Sales Invoices with the payer as customer, pooled orders → Purchase Orders, double-entry batches → Journal Entries with balance re-asserted) through an offline JSONL spool with `openhealth_id` idempotency — ERPNext can be down for a week; the books catch up in order, once. See [ADR-0012](docs/architecture/adr-0012-identity-payers-erp-connectivity.md).

Clinical loop proof: `bash demo/clinical-loop.sh` — consult → assess → prescribe → SHR (MedicationRequest + Encounter) → pharmacy dispense → stock decrement + claim draft → allergy-gate rejection, end to end in-memory.

Scale proof: [`benchmark/`](benchmark) — the Nigerian Million-Claim Challenge harness runs the real adjudicator over a deterministic 1M-claim stream (~41k claims/s, p99 0.046 ms, zero invariant violations on the reference VM).

## Architecture in one picture

```
L5  Applications        EMR · CHW app · claims console · analytics · 3rd-party apps
L4  Interop & exchange  FHIR R4 gateway · consent manager · terminology · DHIS2/NHIA/NIMC bridges
L3  Cloud core (K8s)    Postgres HA · FHIR server · Kafka events · MinIO · Keycloak OIDC · GitOps
L2  Connectivity        2G/3G/4G · LGA hub store-and-forward (priority queues) · USSD/SMS fallback
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
         sync-ingest consent-service ussd-gateway wallet-service equipment-service \
         telehealth-service shr-service pharmacy-service notification-service lga-hub; do
  (cd "services/$m" && go test ./...)
done
cargo test --manifest-path services/ledger/Cargo.toml
cargo test --manifest-path services/telemetry-collector/Cargo.toml
for p in adjudicator triage dhis2-bridge procurement analytics chw-tasks lis-bridge erpnext-bridge; do
  python3 -m unittest discover -s "packages/$p"
done
python3 -m unittest discover -s benchmark          # harness self-tests
python3 benchmark/million_claims.py --claims 10000  # smoke-run the challenge
```

**Storage & auth seams** (ADR-0006): services default to in-memory stores (edge, dev,
tests). Set `DATABASE_URL` to bind Postgres on the state plane — registry, sync-ingest
(exactly-once survives restarts), consent (signed artefacts + audit), SHR (resources +
append-only access audit). Set `OIDC_ISSUER` to enforce Keycloak bearer auth on the
claims orchestrator and SHR gateways. Full platform plane:

```bash
docker compose -f infra/compose/platform.yaml up --build
```

## Repository layout

```
apps/        Client apps — offline-first PWA (nurse/CHW EMR), patient app,
             operations dashboards (facility + state), Android shell (WebView/TWA) [ADR-0009]
services/    Go + Rust deployable services (the runtime plane)
packages/    Python domain packages (rules, analytics, ML seams)
db/          State-plane Postgres migrations (idempotent, applied at boot)
shared/      Shared Go modules — authz (OIDC), servex (timeouts/metrics),
             crdt (edge replication), eventbus, otelx (tracing)
fhir/        FHIR R4 implementation guide — Nigerian profiles (Apache-2.0)
openapi/     Claims-gateway contract (NHCX-analogue): eligibility, pre-auth, claims, status
infra/       Dockerfiles (shared Go/Rust builds) · compose infra + platform planes · Kubernetes skeletons
tools/       ota — signed, staged edge/OTA image channel
benchmark/   Million-Claim Challenge harness (truthful scoring, CI-checked invariants)
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

## Production readiness

The reference implementation ships with the controls a state deployment
needs from day one:

- **Durable money** — the ledger journals every accepted op with fsync and
  recovers exactly across restarts, including in-flight two-phase
  transfers ([ADR-0007](docs/architecture/adr-0007-ledger-durability.md)).
- **Durable state** — every service has a Postgres store behind the same
  interface as its in-memory store; migrations are idempotent and run as
  a Kubernetes job ([ADR-0006](docs/architecture/adr-0006-state-plane-persistence.md)).
- **Identity** — stdlib-only OIDC (RS256/JWKS) on all state-plane
  services, with the Keycloak realm checked in as code; edge facilities
  keep running with the identity provider down ([ADR-0008](docs/architecture/adr-0008-production-security-baseline.md)).
- **Hardened runtime** — restricted Pod Security, seccomp, read-only
  root filesystems, dropped capabilities, and default-deny
  NetworkPolicies in `infra/k8s/`.
- **Operability** — every service exposes `/healthz` and `/metrics`,
  serves with bounded timeouts, and drains connections on SIGTERM
  (`shared/servex`).

## Client apps

One PWA core, three vessels ([ADR-0009](docs/architecture/adr-0009-client-apps-offline-pwa.md)):

- **`apps/pwa`** — the nurse/CHW app and the PHC's EMR: offline-first
  (service-worker shell + IndexedDB outbox + CRDT replica, with an in-memory
  fallback so embedded WebViews can never hang the app), patient chart with a
  unified timeline (encounters · vitals · labs · meds · appointments), vitals
  capture with WHO/IMCI danger bands (age-aware fast-breathing, SpO₂, BP
  crisis, MUAC SAM/MAM), CPOE-lite lab orders, allergy-gated prescribing,
  appointments + today's queue, cohort tags (ANC/EPI derived, HIV/TB/NCD
  clinician-set), in English · Pidgin · Hausa · Yorùbá · Igbo. ~110 KB, no
  framework, runs on Android 7 WebViews.
- **`apps/dashboards`** — facility board (sunlight-readable daylight theme:
  cold chain, power, water, tickets, activity) and state situation room
  (dark ops theme: live facility map, pilot gates, alerts, platform health).
  Both render last-known data offline and never fake liveness.
- **`apps/patient`** — the patient's own app: sign-in with patient code + phone
  (mismatches are 404 — no enumeration), offline feed with action-needed
  grouping (referrals, pickups, lab-ready within 14 days surface first),
  relative timestamps, five languages, calm daylight theme. SMS notifications
  (notification-service) reach feature phones too; USSD stays a peer channel.
- **`apps/android-shell`** — thin native WebView wrapper (minSdk 24) with a
  bundled PWA snapshot for zero-connectivity first launch; TWA for Play
  distribution.

The api-gateway serves the apps at `/app`, `/dash` and `/patient` on the same
origin as `/api/*` (including `/api/notify` for the patient feed) — one port,
one trust boundary, no CORS.

Runbooks: [deploy](docs/runbooks/deploy.md) ·
[backup & restore](docs/runbooks/backup-restore.md) ·
[edge node](docs/runbooks/edge.md) ·
[LGA hub](docs/runbooks/lga-hub.md) ·
[90-day pilot go-live](docs/runbooks/pilot-90-day-go-live.md).

## Contributing

OpenHealth OS is built in the open and upstreams improvements to the commons it composes (OpenMRS, OpenELIS, DHIS2, Medplum, OpenHIE). See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
