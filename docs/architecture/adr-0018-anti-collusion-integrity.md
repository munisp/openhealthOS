# ADR-0018: Anti-collusion integrity plane — insider fraud and theft

**Status**: accepted (2026-10-19) · extends ADR-0012 (identity/payers),
ADR-0015 (hardening), ADR-0017 (enrolment registry, biometrics, portal).

## Context

The question was direct: *how do we ensure hospitals, clinics, and
facilities do not collude to provide care outside the platform — insider
fraud and theft?*

The honest answer has two halves, and this ADR implements both:

1. **Off-platform care cannot be prevented.** No software stops a
   facility from treating patients in a back room for cash. What the
   platform controls is *money and evidence*: care delivered outside the
   platform earns nothing from the payer, and care claimed inside the
   platform must prove it happened. Collusion becomes **unprofitable**.
2. **On-platform fraud can be made visible.** Ghost claims, phantom
   patients, stock theft, and double-dipping all leave arithmetic or
   testimonial evidence. The platform's job is to collect that evidence
   at the moment it is created and surface it where someone can act.

### Threat model (insider + facility collusion)

| Threat | Shape | Structural answer |
| --- | --- | --- |
| Ghost claims | Facility bills for care never delivered | Linkage gate (no encounter → PEND) + patient-as-auditor receipts + disputes |
| Phantom patients | Insider enrols ghosts, then ghosts claims against them | Enrolment requires a named actor, chained to the audit log; biometrics dedupe bodies (ADR-0017) |
| Stock theft | Drugs claimed-as-dispensed, sold privately | stockcheck: claim-vs-dispense-vs-procurement reconciliation |
| Double-dipping | Claim the payer AND charge the patient cash | Patient receipt SMS names the amount — a patient told "your coverage paid ₦12,500" does not also pay cash quietly |
| Enrolment abuse | Suspend a rival's members, enrol ineligibles | Every enrolment state change names its actor and lands in the hash-chained audit file |
| Evidence tampering | Insider edits the logs after the fact | Hash-chained audit file, verified independently by `auditlake` |

## Decisions

### 1. Linkage gate in the adjudicator (`packages/adjudicator/linkage.py`)

**Money follows encounters.** A claim without an `encounter_id` matching
a platform encounter (same member, same facility, same date) cannot be
cleanly approved — it PENDs for human review, never DENYs, because edge
facilities sync late and a late-synced encounter must not kill honest
claims. Presence strength is priced into review priority:
`biometric > card > self` (`PRESENCE_RANK`); self-attested presence adds
a sampling flag. The encounter feed is a JSONL seam (`ENCOUNTER_FILE`),
tolerant of malformed lines; a missing feed is an honest empty, not an
error.

### 2. The patient as auditor (claim receipts + disputes)

Every adjudicated non-DENY claim fires a `claim_submitted` event to the
notification rail. The patient receives a plain SMS — amount, facility,
date, and a free report channel — and sees the same list in the app
(Care tab → Claims) and via `GET /claims/for-member`. A ghost claim now
has two witnesses: the evidence chain and the person who was allegedly
treated.

Disputes are **attributed, ownership-checked, and idempotent**:
`POST /claims/{id}/dispute` requires actor + reason ("an anonymous
dispute is a weapon; an attributed one is evidence"); the portal gate
verifies the claim belongs to the caller (`claimOwnedBy`); the first
accuser stands — a repeat dispute returns success without rewriting the
record. Disputes land in the audit chain and feed the scorecard.

### 3. Hash-chained audit file (`AuditChain` in claims-orchestrator)

Every sensitive mutation — adjudication, dispute, enrolment, suspension —
is appended to a JSONL chain with **the same law as `packages/auditlake`**:
`hash = sha256(canonical(entry minus hash) + prev_hash)`, canonical =
sorted keys, tight separators. The Go writer is cross-verified by the
Python verifier in CI (`TestAuditChainCrossVerifiedByAuditlake`,
including a tamper-detection case), so "we changed the log" is
detectable by an independent tool an auditor already has. Writes fsync
before the action reports success; the chain resumes from the last hash
on restart.

### 4. Stock reconciliation (`packages/stockcheck`)

Three JSONL feeds per facility per drug — claim lines, pharmacy
dispensing, procurement receipts — reconcile into three questions:

- **GHOST_DISPENSE?** claimed − dispensed > tolerance: billed for stock
  that never moved;
- **SHRINKAGE?** procured − dispensed > tolerance: stock that entered
  and never left through the front door;
- **UNPROCURED_DISPENSE?** dispensed > procured: undocumented stock
  source.

Verdicts are **OK / REVIEW — questions, never accusations**. Tolerances
are explicit (`--tolerance-pct`, `--tolerance-units`) because real stock
counts are never perfect. Feeds are read tolerantly: a missing file is
an honest empty.

### 5. Facility integrity scorecard (`GET /integrity/{facility}`)

Aggregates per facility: claims, approvals, pends, denials, disputes,
linkage failures, biometric-presence share, dispute/pend rates, and a
letter grade with **the formula in the open**:

> start 100; −25 per dispute, −10 per linkage failure, −15 if pend
> rate > 30%, −10 if biometric share < 25% (with ≥10 claims).
> A ≥ 85, B ≥ 70, C ≥ 50, else D.

A grade you cannot recompute yourself is an accusation, not a
measurement — reviewers act on the numbers; the grade is a triage aid,
never a verdict.

### 6. Enrolment actor audit

Phantom patients start at enrolment, so `POST /enrolments` now requires
a named `actor` (400 otherwise), and every enrol/suspend lands in the
audit chain as `enrol_member` / `suspend_member` with actor attached.
Combined with ADR-0017 biometric dedupe (one body, one identity), the
ghost-patient supply line closes at both ends.

## What this does not do (and why)

- **It does not prevent off-platform care** — nothing can. It removes
  the payer money from off-platform care and prices evidence into
  on-platform money.
- **It does not auto-deny** — linkage failures PEND because edge sync is
  eventually consistent; fraud signals feed review queues and
  scorecards, not silent verdicts (same doctrine as `frauddetect`'s
  advisory scorer).
- **The scorecard does not name thieves** — it ranks questions. Sanctions
  remain a human, due-process act.
- **The audit chain is not a blockchain** — it is a tamper-evident file
  one auditor can verify with a 100-line Python tool. Deliberately
  boring; boring gets verified.

## Consequences

- New env seams: `AUDIT_FILE` (durable chain), `NOTIFY_EVENTS` /
  `NOTIFY_URL` (claim receipts), `ENCOUNTER_FILE` (adjudicator linkage).
- `Claim.encounter_id` is part of the claim contract; `Decision.linkage`
  and `ClaimResponse.linkage` expose the verdict to callers and the
  scorecard.
- Patient app grows the Claims card (list + dispute, five languages);
  the portal grows `/portal/claims` + `/portal/claims/dispute` behind
  the same patient+phone gate with claim-ownership checks.
- Memory stores remain the default query views; the chain file is the
  durable artefact (Postgres projection is a follow-up, same pattern as
  ADR-0017 stores).

## Verification

- `packages/adjudicator`: 53 tests + 11 linkage tests (ghost pends,
  forged reference, mismatch names both sides, presence sampling,
  malformed-feed tolerance).
- `claims-orchestrator`: store/dispute idempotence, audit chain
  cross-verified by auditlake with tamper case, open grade formula,
  HTTP surface (dispute/scorecard/for-member).
- `notification-service`: receipt/dispute SMS renders (<320 chars),
  member_id addressing, portal claims proxy + ownership tests.
- `packages/stockcheck`: 11 tests (ghost dispensing, shrinkage,
  tolerances, feed tolerance, CLI).
- Patient app: `claimRows`/`nairaText` unit tests; `demo/integrity.sh`
  runs the whole story end to end (9 steps, PASS).
