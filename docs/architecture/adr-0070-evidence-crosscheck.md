# ADR-0070: Evidence cross-check — a gate that cannot see its own evidence rot is decoration

Date: 2026-11-10 · Status: accepted

## Context

`packages/shipgate` (ADR wave 8) states production readiness per
capability: evidence in, remainders named and owned, three verdicts —
`proven` / `stated-remainder` / `unsubstantiated` — and deliberately no
"production ready" verdict. But until this wave the evidence entries
were *pointers the gate never followed*. A capability could cite
"tamperproof demo — byte-identical reruns" and the gate would say
`proven` even if `tamperproof.json` had drifted red an hour earlier.

The estate already had the instrument to close this: `proofindex`
re-verifies every stamped evidence payload and publishes a per-payload
verdict (verified / drift / absent / malformed / …). The wave-6 lesson
(anchorproof.json spent one wave stamped but unregistered — "a registry
that falls behind silently is a blind spot") made the failure mode
concrete: any layer that cites evidence without checking it is a blind
spot of the same shape, one level up.

## Decision

**Readiness claims must be re-verified against the proof index at
assessment time.**

1. Evidence entries MAY name a stamped payload
   (`"payload": "apps/pwa/<name>.json"`). A payload ref must be
   registered in `proofindex.PAYLOADS` — an unregistered ref is void
   on sight, and the register-level invariant is pinned by test
   (`test_every_payload_ref_is_registered`).
2. `shipgate.assess(index=…)` takes an injected proofindex
   `verify_all()` report. Every payload-named piece of evidence is
   re-checked: status `verified` substantiates; anything else
   (`drift`, `absent`, `malformed`, `unregistered`, …) is **void**.
3. Voided evidence is **named, never silently dropped**: it leaves the
   substantiating set and appears on the row as
   `evidence_alerts: [{payload, status}]`.
4. Verdicts are computed from the evidence that *survives*. A
   capability whose evidence all voids falls to `unsubstantiated`,
   however impressive its title — the adjective rule now applies to
   rotten proof, not just missing proof.
5. The verdict vocabulary stays three words. Evidence rot is not a
   fourth verdict; it is a named alert on the row and a counter in the
   summary (`evidence_crosscheck: {run, payload_refs, voided,
   all_verified}`).
6. `assess()` without an index is legal but **stated**
   (`evidence_crosscheck.run = false`) — never implied. A readiness
   report that did not re-check its evidence must say so.
7. `shipgate` never imports `proofindex`: the report is injected, like
   a clock. Both packages stay standalone stdlib (ADR-0005), and the
   drill fixtures can feed the gate synthetic rot without touching the
   disk.

## Consequences

- Six capability entries now name payloads (anchorproof ×2,
  tamperproof, deployproof ×2, agentwrite). The demo board re-runs the
  cross-check against the live tree on every export and shows a
  `✓ payload verified` badge — or a red `void:` badge — per claim.
- The demo's drill set grew from two attacks to four: *claim with no
  evidence* (unsubstantiated), *remainder with no owner* (refused),
  *evidence drifted after it was cited* (unsubstantiated, alert
  named), *evidence the index never registered* (unsubstantiated).
- A future edit that rots a stamped payload now downgrades the
  readiness board on its next deterministic regeneration — stated,
  loud, and in the same gate that ministries will read.
