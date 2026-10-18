# Fraud & integrity: how OpenHealth resists collusion, insider fraud, and theft

> The operating question: *how do we ensure facilities do not collude to
> provide care outside the platform, or defraud it from inside?*
>
> The honest answer: **off-platform care cannot be prevented — it can be
> made unprofitable; on-platform fraud cannot be wished away — it can be
> made visible.** This document is the map. Implementation detail lives
> in [ADR-0018](architecture/adr-0018-anti-collusion-integrity.md).

## The principle: money follows encounters, and every claim has two witnesses

A claim earns payer money only when it is tied to a platform encounter —
and every claim is receipted back to the patient in plain SMS with the
amount, the facility, and a free way to say *"that never happened."*
A facility that treats patients off-platform earns nothing from the
payer for it; a facility that ghosts claims on-platform is exposed by
the two people who always know the truth: the evidence chain and the
patient.

## Threat model → control map

| # | Threat | What it looks like | Control | Where |
| --- | --- | --- | --- | --- |
| 1 | **Ghost claims** | Bill for care never delivered | Linkage gate: no matching encounter → PEND (never silent approval, never auto-DENY — edge syncs late) | `packages/adjudicator/linkage.py` |
| 1b | | Ghost claims against real members | Patient receipt SMS + Claims list in the app + attributed dispute | `claim_submitted` event, `/portal/claims` |
| 2 | **Phantom patients** | Insider enrols a ghost, claims against it | Enrolment requires a named actor, hash-chained; biometric dedupe = one body, one identity | `/enrolments` actor gate + `AuditChain`; ADR-0017 biometrics |
| 3 | **Stock theft** | Drugs claimed-as-dispensed, sold privately | Three-way reconciliation: claim lines vs dispensing vs procurement; OK/REVIEW, tolerance-aware | `packages/stockcheck` |
| 4 | **Double-dipping** | Claim the payer AND charge the patient cash | The receipt SMS names the amount — a patient told "your coverage paid ₦12,500" does not quietly pay cash too | notification rail |
| 5 | **Enrolment abuse** | Suspend rivals' members, enrol ineligibles | Every state change names its actor and lands in the chain | `enrol_member` / `suspend_member` entries |
| 6 | **Evidence tampering** | Edit the logs after the fact | Hash-chained audit file; verified independently by `auditlake` (tamper = broken chain) | `AuditChain` + `packages/auditlake` |
| 7 | **Facility-level patterns** | One facility's numbers drift from peers | Integrity scorecard: disputes, linkage failures, pend rate, biometric share — formula in the open | `GET /integrity/{facility}` |
| 8 | **Claims-level anomalies** | Upcoding, duplicates, velocity, spikes | Advisory fraud signals (never auto-deny) feeding the same review lane | `packages/frauddetect` |

## The scorecard, in the open

```
score = 100
      − 25 × disputes            (a patient said "this care didn't happen")
      − 10 × linkage failures    (claim couldn't prove the encounter)
      − 15 if pend rate > 30%    (the review lane is a cost the facility chooses)
      − 10 if biometric share < 25% and ≥10 claims (weakest presence proof)

A ≥ 85 · B ≥ 70 · C ≥ 50 · D < 50
```

A grade you cannot recompute yourself is an accusation, not a
measurement. Reviewers act on the numbers; the grade is a triage aid,
never a verdict. Sanctions remain a human, due-process act.

## What a reviewer does with a REVIEW

1. **GHOST_DISPENSE?** — ask the facility for the dispensing register
   and the patient encounter notes for those claim lines.
2. **SHRINKAGE?** — count the shelf, check the register, walk the
   cold-chain log (`packages/coldchain`).
3. **Dispute cluster** — call the patients; three disputes with the
   same shape is a pattern, not a coincidence.
4. **Grade D with rising linkage failures** — schedule an announced
   audit; the chain file travels with the auditors.

## Deliberate non-goals

- **No auto-denial on fraud signals.** Edge Nigeria syncs late; an
  innocent facility must never be silently defunded by a heuristic.
- **No anonymous disputes.** An unnamed accusation is a weapon;
  an attributed one is evidence.
- **No blockchain.** The audit chain is a tamper-evident file verifiable
  by a 100-line Python tool. Boring gets verified.
- **No accusation language in tooling.** `stockcheck` says REVIEW and
  asks questions; it never prints THEFT or FRAUD.

## Try it

```bash
bash demo/integrity.sh
```

Nine steps: ghost claim PENDs, linked claim approves, patient lists and
disputes, anonymous dispute refused, audit chain verifies (and a
tampered copy fails), stock reconciliation flags 460 ghost-dispensed
units, and the facility's scorecard drops to grade C — all reproducible,
all in the open.
