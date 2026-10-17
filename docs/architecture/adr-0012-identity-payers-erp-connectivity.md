# ADR-0012: Identity assurance, payer network, ERP composition, and the connectivity contract

**Status**: accepted · **Date**: 2026-10-18

## Context

Four questions arrive together at national scale, and they share one
theme — the platform must trust identities, money routes, and books
*without ever making connectivity a precondition of care*:

1. **Identity/KYC.** A platform that pays claims on patient records is a
   fraud target: ghost patients, duplicate enrolments, one agent
   registering fifty "patients" on their own phone. But Nigeria's reality
   is progressive enrolment — many patients have no NIN yet, and NIMC
   verification needs connectivity the edge often lacks.
2. **Payers.** Coverage is a federation: NHIA schemes (BHCPF, state basic,
   formal sector) plus commercial HMOs, each with its own submission
   endpoint. The adjudicator decides *whether* a claim pays; something
   must decide *where* it goes, and a claim must never be paid twice.
3. **ERP.** Facilities and state offices need accounting, inventory, and
   procurement books. ERPNext (GPL-3.0) is the natural open-source fit —
   but the platform's ledger and stock must remain the systems of record.
4. **Connectivity.** "Telehealth" in a 2G world cannot mean live video.

## Decision

### 1. The KYC ladder — assurance without exclusion (registry-service)

Four levels, monotonically increasing, **never blocking care**:

| Level | Meaning | How it is earned |
|---|---|---|
| `self` | demographics as stated at registration | default at enrolment |
| `phone` | handset proven | 6-digit OTP (SHA-256 stored, 10-min TTL, 5-attempt lockout, one-time use) |
| `nin-format` | NIN present, 11-digit check | offline, at registration or NIN bind |
| `nin-verified` | NIMC confirms NIN + DOB match | `POST /patients/{id}/verify` via the `Verifier` seam |

- **Verifier seam** (`NIN_VERIFY_URL`): offline default defers honestly
  ("verify when online"); any NIMC-compatible endpoint plugs in by config.
  No vendor SDK (ADR-0005).
- **Deterministic dedupe at registration**: same NIN, same phone+DOB, or
  same normalised name+DOB → 409 with the existing record's ID. Fuzzy
  matching is deliberately *not* a gate — probabilistic candidates belong
  to state-plane analytics with human review; registration must be
  explainable to a registrar at a PHC desk.
- **Backstops**: Postgres enforces NIN uniqueness; OTP attempt counters
  are persisted (`UPDATE … RETURNING`) so a crash cannot reset a lockout;
  OTP codes rest only as hashes.
- **Fraud signals for ops** (`GET /kyc/signals`): phones shared by
  multiple patients (normal for families, suspicious at scale), level
  distribution, NINs bound but never authority-verified.
- **Failed verifications are recorded**, not silent — a registrar trying
  random NINs against a record is itself a signal.

### 2. Payer network (packages/adjudicator/payers.py)

The adjudicator's verdict carries the member's scheme; the **PayerRouter**
maps scheme → payer (default registry: NHIA for BHCPF-VUL / STATE-BASIC /
NHIA-FORMAL; commercial HMOs as config entries, `register_payer()` for
newly signed contracts — no code change). Guards:

- **Unknown scheme refused** — an unregistered scheme is a fraud smell;
  the claim never leaves the building.
- **(payer, claim_id) idempotent** — retries return the same reference.
- **Cross-payer replay refused** — a claim routed to NHIA cannot also be
  submitted to an HMO (`CrossPayerReplay`). Double-dipping across payers
  is the classic HMO fraud pattern, and it is structurally impossible here.
- Only `APPROVE` decisions are ever submitted, per-payer sandbox/live seam
  (deterministic offline references; live = POST `{claim, decision}` with
  bearer token).

### 3. ERPNext as the finance office's book — composed, never embedded

ERPNext is GPL-3.0; we integrate over its REST API and redistribute
nothing (ADR-0005 compose-don't-fork holds; the network boundary keeps
licences independent). `packages/erpnext-bridge`:

- **System of record stays ours.** The Rust ledger, pharmacy stock, and
  procurement pooling are truth; ERPNext receives projections:
  stock receive/dispense → Stock Entry; APPROVEd claim → Sales Invoice
  (the payer is the customer); pooled order → Purchase Order; double-entry
  batch → Journal Entry (the mapper **re-asserts debit = credit** — an
  unbalanced batch can never reach the books).
- **Offline spool**: JSONL buffer, atomic rewrite on flush, order
  preserved, link-down stops the pass without losing anything.
- **Idempotency**: every document carries `openhealth_id` (custom field);
  check-before-create means crash-mid-flush and event replays cannot
  double-post.
- **Malformed events drop loudly** (counted, logged) — one bad event can
  never poison the queue.

### 4. The connectivity contract for telehealth

Telehealth is **asynchronous store-and-forward by design**, not degraded
video: the nurse captures complaint, protocol, vitals, and assessment
offline in the PWA (IndexedDB outbox + in-memory fallback); the consult
state machine (open → assessed → escalated/closed) and AI triage run
entirely on-device; doctor review and escalation ride the same sync rails
as everything else (facility edge → LGA hub → state plane, priority
queues). There is no workflow that requires a live interactive session, so
there is no bandwidth floor below which care stops. Live video, where
connectivity exists, is an enhancement layer over the same consult record
— never the foundation.

### 5. External labs and pharmacies are network participants, not plugins

- **External labs** speak the lis-bridge contract: orders out, results
  back as FHIR Observations with chain-of-custody; the hub-and-spoke model
  (PHC collects, hub lab analyses) is the default Nigerian pattern.
- **Network pharmacies** (a patient takes a prescription to an external
  pharmacy) are claim submitters on the same NHCX-analogue gateway as
  facilities: eligibility check → dispense → claim → adjudication → payer
  routing. No separate integration stack; the same fraud gates (KYC level
  on the patient record, dedupe, cross-payer replay) apply.

## Consequences

- Care is never hostage to identity infrastructure: a patient with no NIN
  and no phone is still enrolled, treated, and claimable; assurance accrues
  as connectivity allows.
- Fraud is fought at three depths: registration gates (deterministic),
  database constraints (uniqueness), ops signals (human review) — each
  layer independent.
- Adding a payer or an ERP is configuration, not engineering.
- The ERP can be down for a week; the books catch up in order, once.

## Verification

- `services/registry-service`: KYC unit/HTTP tests (ladder defaults,
  dedupe gates including family-phone carve-out, NIN verify flow
  verified/no-match/deferred/bad-format, OTP lifecycle with lockout and
  replay, signals, no-downgrade guard) + `TEST_DATABASE_URL`-gated
  Postgres suite covering the same contract (migration 0005 self-applied).
- `packages/adjudicator` — 27 tests including payer routing, idempotent
  retry, cross-payer replay refusal, runtime payer registration.
- `packages/erpnext-bridge` — 8 tests against an in-process fake Frappe
  server: mapper shapes, offline buffering, idempotent flush, loud-drop,
  unbalanced journal never posts.
