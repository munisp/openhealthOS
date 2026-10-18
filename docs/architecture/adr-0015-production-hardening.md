# ADR-0015: Production hardening — closing every named gap on the scorecard

Status: Accepted
Date: 2026-10-18

## Context

`docs/production-readiness.md` scored every feature domain and every named
infrastructure component, honestly: several rows sat at B/B+ with named
gaps, and the directive was to close them — every score to A, with
evidence, without breaking the platform's doctrines (offline-first,
three-language, compose-don't-fork, never embed BSL/SSPL/Elastic).

This ADR records the fifteen decisions that close those gaps. Each names
the row it answers, what ships, and why the obvious alternative was
rejected. None of them is aspirational: every one has a test suite, a
demo, or a CI gate in this repository.

## Decision 1 — Key rotation: the sealx keyring (`SX2:`)

**Row: Security baseline.** Edge sealing used a single AES-256-GCM key
(`SEALX_KEY`) with no rotation story — rotating meant downtime or data
loss. The keyring (`shared/sealx/keyring.go`) makes rotation a config
event: `SEALX_KEYRING="k2=<hex>;k1=<hex>"` (first key is current), seals
carry the key id (`SX2:<kid>:<payload>`), opens try the ring first and
legacy `SX1:` envelopes still open with `SEALX_KEY`. `RotateLine` re-seals
one envelope at a time so a journal sweep migrates lazily — no stop-the-
world re-encryption of an edge node's outbox.

## Decision 2 — Search: Postgres full-text, OpenSearch stays rejected

**Row: OpenSearch (was C).** The licence rejection stands; the capability
gap does not. Migration `0008_fts.sql` adds generated `tsvector` columns
(`'simple'` config — stemming mangles Nigerian names; prefix tokens give
type-ahead) with GIN indexes; the registry exposes AND-prefix search over
facilities (`/facilities/search`) and patients (`/patients/search`) with
memory/Postgres parity tests. At state scale (millions of patients,
thousands of facilities) Postgres FTS is the right tier; a dedicated
search cluster is the documented seam if national-scale relevance tuning
is ever needed.

## Decision 3 — Notifications: provider failover chain + circuit breakers

**Row: Notifications.** One SMS aggregator was a single point of failure
on the OTP and results-ready paths. `chain_provider.go` walks an ordered
provider chain, each leg wrapped in a closed/open/half-open circuit
breaker, with an ops status view (`/providers`). Unknown chain config is
loud (a `brokenProvider` leg), never silently skipped.

## Decision 4 — Tariffs as versioned bundles

**Row: Claims adjudication (fixture caveat).** Tariffs are now versioned
JSON bundles (`packages/adjudicator/bundles/ng-default-2026.json`) with a
loader, validation, and bundle-scoped lookups (`tariffs.py`) — a state
upload is a data event with provenance, not a code change.

## Decision 5 — ML governance: thresholds, catchments, rings, the champion gate

**Rows: Fraud detection, Neo4j, Geospatial batch.**
- `choose_threshold`: champion thresholds are chosen under an explicit
  policy (precision ≥ 0.8 within a 10% review budget) and recorded in
  `metrics.json`; inference reads the threshold from the champion's own
  metrics — a threshold is governance, not a magic constant.
- `catchment.py`: batch geospatial analytics (underserved LGAs, beds per
  capita, member travel distance) — the Sedona-row answer: Spark is the
  wrong tier for one country's nightly planning job.
- `rings.py`: fraud-ring exploration as connected-components over the
  claim graph — the Neo4j-row answer: the exploration job is stdlib
  Python, no graph database required.
- `validate.py` runs in CI: champions must hold an AUC floor against an
  unseen-seed holdout, or the build fails. Fraud-mlp v4 is the reigning
  champion under this gate; claim-gcn v3 was NOT promoted over v2 because
  the gate found no material improvement — the gate bites both ways.

## Decision 6 — Registry data population: facility-import

**Row: Geospatial (registry capacity).** `tools/facility-import` seeds the
registry from DHIS2/HFR CSV or GeoJSON, validates hard (a bad coordinate
puts a facility in the Atlantic and poisons every near-me answer), and
upserts through the registry API — never direct SQL. A governed import
path is the difference between "map renders" and "map is honest".

## Decision 7 — Ledger availability: warm standby + DR runbook

**Rows: Payments & ledger, TigerBeetle.** `tools/ledger-mirror` replicates
the ledger journal to a warm standby (incremental offsets,
truncation-aware on snapshot restore); `docs/runbooks/ledger-dr.md` is
the failover drill. Multi-writer consensus remains the documented
scale-out step and a deliberate non-goal for go-live: the platform's
design point is one writer per state plane, exactly like its single
Postgres primary.

## Decision 8 — Optional shared cache: cachex (RESP2 + LRU failover)

**Row: Redis (was B).** Platform services were deliberately Redis-free;
the one place a shared cache genuinely earns its keep is JWKS. `shared/
cachex` is a stdlib RESP2 client + a TTL LRU + a Fallback wrapper:
`CACHE_URL=redis://…` binds any RESP-compatible server (Redis, or Valkey
— BSD — per the licence doctrine); unset, every process still gets a
local LRU. The cache is an accelerator, never a dependency: primary
failure degrades to the shadow LRU with an event hook.

## Decision 9 — JWKS warm standby

**Rows: Keycloak, Redis.** `shared/authz` now writes every successfully
fetched JWKS through to the shared cache (24h TTL) and serves verification
from that standby when the IdP is unreachable. A 30-second IdP blip no
longer rejects clinicians; a cold replica with no cache still fails
closed. Tested: two-replica outage, tampered-token rejection through the
standby path. Every gateway replica wires this in `main()`.

## Decision 10 — Edge screening: wafx, OpenAppSec stays unembedded

**Row: OpenAppSec (was B).** `shared/wafx` implements the ingress WAF's
job as four stdlib controls at the gateway: method allowlist (405),
content-type enforcement for bodies (415), body size caps (413, with a
streaming cap for chunked liars), and probe screening on path+query
(400) — SQLi shapes, traversal, XSS probes, null bytes. Screening is
deliberately multi-signal: "O'Brien Memorial" searches pass. JSON bodies
are NOT screened — clinical free text legitimately contains quotes and
angle brackets; body-level injection resistance comes from parameterized
queries everywhere, and a false positive on a referral note is worse than
the marginal coverage. Screening runs before auth, so junk never costs
JWT crypto.

## Decision 11 — Gateway resilience: per-upstream circuit breakers + request-id

**Row: APISIX.** The gateway now wraps every upstream in the same
closed/open/half-open breaker as the SMS chain: 5xx and transport errors
trip it, 4xx never does; an open breaker fails fast with 503 +
Retry-After instead of every request paying the upstream timeout;
`/api/health` surfaces every breaker's state and counters. Every request
carries an `X-Request-ID` (client's own propagated, else minted) echoed
on the response and forwarded upstream — one id greppable across the
whole call chain.

## Decision 12 — Event spine: the dead-letter queue

**Rows: Kafka, Fluvio.** At-least-once delivery has one fatal shape: the
poison message parked at the head of a consumer group forever.
`shared/eventbus/dlq.go` adds Kafka-grade semantics to the seam: delivery
attempts persist in an AttemptStore (memory or fsynced files); after
`MaxDeliveries` the event is parked on `<topic>.dead` with its failure
history, the offset commits, and the topic flows. Parking must never
become silent loss: if the dead-topic publish fails, the offset stays
behind. `Replay` republishes parked events after the handler is fixed.
Fluvio remains not adopted — with the DLQ, the event spine covers its
niche completely; a second streaming layer would be redundancy.

## Decision 13 — ReBAC: tuples, the derived rule, and one enforced consumer

**Row: Permify.** `shared/rebac` is the relationship-tuple store
(Zanzibar-shaped, memory + Postgres behind migration `0009_rebac.sql`)
plus a deliberately small checker: direct patient grants (guardian,
care-team, referral) or staff/admin of the patient's registering facility
— nothing else. The registry enforces it (`REBAC_ENFORCE=true`): patient
reads require the gateway-set `X-User-ID` and a live grant, unattributed
access is 403 (same doctrine as the SHR consent gate), and grants are
managed over HTTP (`/rebac/grants`) by facility admins, with
`REBAC_BOOTSTRAP_ADMINS` solving the bootstrap problem. The gateway now
forwards `X-User-ID` from the OIDC subject alongside `X-Facility-ID`.

## Decision 14 — NIMC conformance: the sandbox binary

**Row: Identity & KYC.** `tools/nimc-sandbox` is a deterministic stand-in
for the national identity authority speaking exactly the HTTPVerifier
wire contract. The registry's conformance suite builds the real binary
and runs the real verifier against it over real HTTP: verified, DOB
mismatch, unknown NIN, bad format, wrong token, authority down — every
authority outcome has a defined effect on the KYC ladder. This also fixed
a real bug: non-200 authority responses (bad credentials, rate limits)
now defer instead of condemning a patient to `no_match`. Swapping in the
licensed NIMC endpoint is a configuration change, proven.

## Decision 15 — Finance configuration: the COA bundle + batch rollups

**Rows: ERP, Analytics.** `packages/erpnext-bridge/coa.py` + `bundles/
coa-ng-default.json`: platform ledger codes (SETTLE-POOL, PSP-FLOAT,
WALLET-*, PREMIUM-*) map to ERPNext GL accounts by exact or prefix rule;
unmapped codes are hard errors at flush; bundles validate at load;
`validate_remote` checks every mapped account exists on the live server
before the bundle is trusted. `packages/analytics/rollups.py` closes the
batch-analytics gap: per-LGA and per-state rollups (facilities, capacity,
amenity coverage, claims, stock-outs) plus the ministry-brief renderer.

## Consequences

- Every named infrastructure row now has its capability shipped and
  tested in-platform or explicitly rejected with the alternative shipped
  (see the updated scorecard).
- The gateway is the platform's hardened edge: WAF screening → request-id
  → OIDC (with JWKS standby) → rate limits → per-upstream breakers.
- One honest caveat survives, by design: the fraud champions are trained
  on calibrated synthetic data. The continuous-retrain pipeline retrains
  on platform data as it accumulates (dataset-hash trigger), and the CI
  gate re-validates every new champion — but real-world model validation
  requires production claims, which require go-live. That is stated on
  the scorecard in the same breath as the score, per the method note.
