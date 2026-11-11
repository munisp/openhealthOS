# ADR-0087: The feature ledger is governed data — one ascending sequence, numbers never collide, gaps stated

- Status: accepted
- Date: 2026-11-11
- Wave: 23 (README row 228)

## Context

Wave-23 recon censused the README feature ledger itself — the public
census of *what shipped*, cited by boards, ADRs and memory — and found
it had silently decayed across six independent faults:

- **Row 30 was claimed twice**: the Provider economics row (ADR-0032)
  in sequence, and the Benefit visibility row (ADR-0031) displaced into
  an orphaned block after row 145.
- **Rows 155–160 were claimed twice**: the travelpass engine rows
  (vhsupport, vhmarts, vhease, vhclearance, vhcover KeyRing, vhnotify)
  and the demo block (vhpilot, vhgolive, vhdemo, vhc-walkthrough,
  audience-aware, Cabo Verde tailoring) — two waves numbered against
  the same range three sections apart.
- **Row 36 was never allocated** — a silent gap.
- **Five rows sat displaced** (85, 71, 30, 20, 19) after row 145
  instead of in numeric position; row 71 was separated from its own
  sub-row 71a, which waited between rows 70 and 72 for a parent that
  wasn't there.
- **Two order inversions**: modules-table rows 10/11 transposed, and
  rows 37/38 inverted against their own ADR numbers (ADR-0039 before
  ADR-0038).
- **A suffix anomaly**: `94b` existed with no `94a` — the suffix
  sequence never started.
- **A convention collision**: rows 221/220/219/218 ran newest-first
  descending against the ascending body — an insertion habit, not a
  contract.

Nothing watched the ledger: the estate gates its payloads, shells,
geodesy, evidence freshness and (since wave 22) its decision chain,
but the record of *what* was ungoverned.

## Decision

1. **A row number is a contract.** Two rows claiming one number is a
   defect — red, with every claimant named by line and excerpt.
   Renumber, never reuse: ADR-0031's displaced row took the only free
   number (36); the demo block — the later claimant — moved to
   222–227. Zero inbound references existed to any renumbered row
   (verified by whole-repo grep before the edit).

2. **The ledger is one ascending sequence.** Out-of-order is red. The
   newest-first descending tail was an insertion habit; it is retired
   and the tail now runs ascending like the body. New waves append.

3. **A gap is not a defect** — a row can be retired — **but a gap is
   STATED**, amber; silence is the defect.

4. **Sub-rows attach to their parents** (`8a`, `71a`, `94a`): they must
   follow the parent directly, and the suffix sequence starts at `a`.
   An orphan sub-row is red; a broken suffix sequence is amber, stated.
   `94b` was renamed `94a` (sole reference was the row itself).

5. **The ledger is gated.** `packages/ledgergate` audits README.md with
   an injected reader (`audit(root, read=...)`) — fixtures and the real
   tree run the exact same code path. The gate is adopted by its own
   estate in the same wave: ledgerproof demo + board + proofindex
   registration (payload 34, recipe 27) + refresh-rail census pins +
   service-worker cache + this ADR.

## Consequences

- The live ledger is provably honest: 227 rows, 1..227, no duplicates,
  no gaps, fully ascending, three sub-rows attached — and a freshness
  gate means any future hand-edit that drifts goes red in the suite.
- The meta-gate now has 27 exporters; the refresh rail regenerates all
  34 registered payloads from one recipe book.
- Renumbered rows: 30→36 (Benefit visibility, ADR-0031), 155–160→
  222–227 (demo block), 38→37 (ADR-0038) and 37→38 (ADR-0039) swapped
  into ADR order, 94b→94a. All are stated here and on the board.

## Alternatives considered

- **Leave the descending tail as convention.** Rejected: two ordering
  conventions in one ledger is exactly how the 155–160 double claim
  went unnoticed — the eye cannot verify what has two shapes.
- **Teach the gate the tail.** Rejected per ADR-0086's precedent:
  normalise the data, never teach the gate a variant.
- **Gaps as red.** Rejected: retirement is legal; *silence* is the
  defect. Amber stated matches the ADR-chain gate.
- **Insert the demo block between rows 160 and 161 by shifting 161–221
  up six.** Rejected: 61 published row numbers would move; renumber the
  six later claimants instead — the smallest reference graph moves.
