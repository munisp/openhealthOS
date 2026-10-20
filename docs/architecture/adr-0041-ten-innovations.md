# ADR-0041: Ten platform innovations — premiere-tier feature set

Status: accepted (2026-10-20)

## Context

The platform reached feature-completeness against its reference spec
(ADR-0036). This ADR records ten innovations that push past parity into
differentiation. Each is a small, independently deployable unit; all obey
the standing doctrine (stdlib-only Go, Python off the hot path, fail-soft
integrations, OSI-clean licences, tested).

## Decision

1. **Unified patient timeline** — `notification-service` merges its own
   notification store with the telehealth consult leg into one
   newest-first `GET /portal/timeline`, gated by step-up elevation
   (ADR-0039). The patient PWA gains a Timeline tab. Legs fail soft:
   a dead telehealth service degrades the entry to `Complete=false`,
   never the whole timeline.
2. **Medication adherence engine** — `notification-service` watches
   `prescription` / `medication_dispensed` events and runs a 30-day
   course plan: once-daily morning nudges, refill reminders inside a
   3-day lead window, and lapse escalation to ops contacts. Idempotent
   re-arm on repeat prescriptions.
3. **Adaptive outbreak detection** — `packages/surveillance` adds
   `evaluate_adaptive`: z-score against a trailing 8-week baseline per
   (syndrome, area), 3σ threshold, minimum 4 baseline weeks, absolute
   k-anonymity floor. Catches bursts in areas whose absolute counts are
   too small for fixed thresholds.
4. **Smart slot recommendations** — `scheduling-service` gains
   `GET /slots/recommend`: ranks facilities by earliest next-free slot,
   ties broken by lowest load. Turns "find an appointment" from search
   into one call.
5. **CHW visit routing** — `packages/routing/visitplan.py`: nearest-
   neighbour seed + 2-opt improvement over haversine legs (×1.4 road
   factor), with a `graph_route` hook for real road graphs where
   available. Daily community-health-worker rounds as a solvable plan.
6. **Multilingual danger-sign escalation** — the WhatsApp gateway
   pattern-matches danger phrases (heavy bleeding, baby not moving,
   convulsions, …) in English, Pidgin, Hausa, Yorùbá and Igbo, and
   short-circuits any dialogue state to escalate to the howUdeyChat
   clinical room. Detection beats dialogue correctness.
7. **Pre-payment fraud gate** — `claims-orchestrator` scores every
   APPROVE-and-payable claim through `packages/frauddetect` (subprocess
   seam, 2 s timeout) before money moves. Hot claims → `PEND_REVIEW`
   (the scorer never auto-denies); scorer outage fails **open** with an
   audit note so payment continuity outranks one review. All holds and
   fail-opens land in the audit chain.
8. **Facility readiness index** — `services/facility-readiness`: rolling
   windows of cold-chain (2–8 °C WHO EPI), power-uptime and water
   readings → weighted 0–100 score with READY / DEGRADED / NOT_READY /
   UNKNOWN grades. Missing sensors redistribute weight instead of
   penalising; no data is honestly UNKNOWN, never a reassuring zero.
9. **Closed-loop referral tracker** — `services/referral-service`:
   OPEN → ACCEPTED → COMPLETED state machine with SLA sweep; breaches
   fire `referral_breached` to the notification rail exactly once. The
   patient who never arrives is now visible.
10. **Tamper-evident audit anchors** — `packages/auditlake` adds
    `anchor` / `verify-anchor`: Merkle roots over the hash chain,
    committed to an append-only anchors file. The chain proves internal
    consistency; anchors defeat whole-chain rewrites, content edits,
    and tail deletions — and survive legal growth after anchoring.

## Consequences

- Two new services (`facility-readiness` :8093, `referral-service` :8094)
  join the compose plane; both are stdlib Go, in-memory first, Postgres
  behind the same store interfaces later.
- The fraud gate adds one subprocess call (~10–30 ms) to the claim
  hot path — acceptable against ledger+settlement latencies, tunable
  via `FRAUD_HOOK=off` on python-free edge nodes.
- Danger-sign matching is substring-based: cheap, explainable, and
  biased to false positives (safe direction for triage).
- Every innovation carries unit tests; the full suite stays green.
