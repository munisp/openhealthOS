# ADR-0090: One sketch port — Apache DataSketches as the main sketch engine, adopted by the lakehouse

- Status: accepted
- Date: 2026-11-14
- Wave: 26 (README row 231)
- Amends: ADR-0005 (scoped — see Decision 2)
- Refused: the datasketches-postgresql deployment form (see Refusals)

## Context

The platform's analytics answer to "how many distinct patients,
deduplicated, at national level" was: scan raw rows. The ministry
rollup problem — facility → LGA → state → national — makes exact
distinct-counting expensive in exactly the environments this platform
serves: raw extracts rescan per level, or raw identifiers travel
upstream over thin links. Cohort questions ministries actually ask
(HIV ∩ TB co-enrollment, enrolled-but-not-seen) are set algebra plain
SQL answers painfully at scale.

Apache DataSketches (Apache-2.0) is the mature family of stochastic
streaming algorithms: theta sketches (distinct counts with set
operations and mergeable unions), kll (quantiles), HLL/CPC (compact
cardinality). Estimates carry provable error bounds — approximation
that can be *declared*, which is what distinguishes it from a lie.

## Decision

1. **`packages/sketchport` is the one sketch port.** Rollup contracts
   are DATA (`REGISTRY`): kind (theta/kll), source event types, id /
   measure field, hierarchy, k-floor, replayability, contractual flag.
   A rollup is a registry entry, never a code branch.

2. **The `datasketches` Python bindings are the MAIN engine.** Main
   means main: the suite pins `ADAPTER == "datasketches"`; wheel
   absent is a red gate. The stdlib fallback (exact sets / sorted
   lists) is DECLARED degraded mode — exact but unbounded in memory
   and without mergeable sketch bytes — stamped on every receipt.
   ADR-0005 is amended in one more scoped place: sketchport holds a
   runtime wheel dependency alongside storageport; every other package
   stays stdlib-only.

3. **The lakehouse adopts the seam.** `lakehouse.build(...,
   sketch_rollups=True)` delegates to `sketchport.gold_rollups` and
   writes `gold/sketch_rollups.json` from the same bronze events —
   sketch rollups are a medallion gold product (ADR-0047 contracts
   unchanged). A seam unadopted by its own estate is decoration.

4. **Approximation is declared.** Every estimate carries
   `{estimate, lower, upper}` at 2 std devs. Exact-mode cells (below
   sketch capacity) collapse bounds to the estimate — also declared.

5. **Sketches never answer contractual counts.** A registry entry
   with `contractual: true` is refused by name. Billing, registries,
   and compliance counts stay exact paths.

6. **kll is non-replayable — stated.** kll compaction is randomized:
   identical input streams serialize differently (verified in the
   wave-26 probe). Quantile estimates are served at query time and
   EXCLUDED from stamped payloads; verdicts against stated windows
   (2x the library's own normalized rank error) are the payload
   citizens. The exclusion is data (`replayable: false`), not silence.

7. **k-floor suppression (ADR-0083 doctrine).** Cells under k=5 are
   suppressed with the floor named — absence is not a value, and
   small cells are never rounded up.

8. **Fail-closed seed discipline.** Theta hashing is seeded from one
   registry constant (`SEED`); the library verifies the seed hash on
   deserialize, and a mismatch surfaces as a stated refusal.

## Refusals

- **datasketches-postgresql (the extension form) — refused.** It wraps
  the same datasketches-cpp core the Python bindings wrap, but as a
  compiled C++ server extension it inverts the stdlib medallion
  architecture (ADR-0047), demands a toolchain on every database
  server the estate touches, and is absent from managed Postgres
  offerings — a deployment-shape dependency this platform cannot
  impose on ministries. The capability is adopted; the form is not.
  Reconsider if a national analytics plane lands on self-hosted
  Postgres with operations capacity.

## Consequences

- Positive: mergeable rollups — facilities compute kilobyte sketches
  and unions merge up the hierarchy without re-touching raw rows;
  cross-facility patients deduplicate at national level; cohort set
  algebra is first-class; rollup cells carry sketch sha256 receipts
  (the checksum is the contract).
- Positive: the degraded path is honest — exact counts with the
  downgrade (memory, mergeability) stated on every receipt.
- Limitations (honest): sketch estimates are approximate — contractual
  contexts are refused, not approximated; kll results are
  non-replayable and excluded from the checksum rail; sketches over
  hashed identifiers help data minimization but are not, by
  themselves, a privacy guarantee.
