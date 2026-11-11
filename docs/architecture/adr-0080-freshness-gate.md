# ADR-0080: The freshness gate — committed evidence must equal today's export

- Status: accepted
- Date: 2026-11-11
- Supersedes: nothing. Extends ADR-0067-era determinism doctrine (the meta
  gate) with the dimension it was missing.

## Context

Wave 5's meta gate (`TestExporterDeterminism`) runs every demo exporter
twice in fresh subprocesses and demands byte-identical output: a demo you
cannot reproduce is a claim, not evidence. Wave 7 added the coverage gate:
every stamped payload on disk must be registered or declared self-excluded.

Wave 17 exposed the blind spot between them. `routing` moved to @1.2.0
(clamped haversine, ADR-0078). The geointproof payload embeds the routing
version in its `versions` member. The exporter deterministically produced
the NEW bytes on every run — run-vs-run stayed green — while the
COMMITTED `geointproof.json` still claimed `routing@1.1.0`. proofindex
verified it, because a stale payload is not drift: its sha256 stamp is
internally consistent with its own (old) content. The estate carried
evidence that no longer represented its machinery, and every gate was
green.

Determinism proves run-to-run sameness. Coverage proves the registry
knows the file. Neither proves the committed evidence equals what the
exporter produces today. That third dimension is freshness.

## Decision

1. **proofindex@1.2.0 gains `freshness_report(fresh, *, root=".",
   payloads=None)`.** The runner is INJECTED: `fresh(entry)` returns the
   bytes (or str) of a fresh export, `None` where the payload has no
   deterministic exporter, and may raise. proofindex never spawns a
   subprocess — the executor is injected like a clock, keeping the
   library stdlib-only (ADR-0005) and unit-testable without processes.

2. **Verdicts are named, never silent:**
   - `fresh` (green) — committed bytes equal a fresh export;
   - `stale` (red) — the machinery moved and the evidence was not
     regenerated; the verdict names BOTH the committed and the fresh
     sha256, because a verdict you cannot audit is decoration;
   - `runner-error` (red) — the exporter raised; a raise is a verdict,
     never a crash;
   - `no-exporter` (amber) — no deterministic recipe is registered, so
     freshness rests on manual discipline — stated, never treated as
     fresh;
   - `absent` (red) — the committed file is not on disk.

3. **`TestExporterFreshness` joins the meta gate** in
   `packages/proofindex/test_proofindex.py`:
   - *File family* (18 payloads): the exporter snippet rewrites the
     committed file; bytes are compared to `git show HEAD:<path>`. A
     payload not yet committed (mid-wave) falls back to a pre-run
     working-tree snapshot — the basis is stated in the failure message.
     On a stale failure the regenerated file is already in the tree,
     ready to commit.
   - *Hash family* (5 exporters, 12 payloads): the exporter prints one
     sha256 per exported file in sorted-filename order; the gate compares
     to digests of the committed files in the same order.
   - A coverage pin asserts every registered payload has a freshness
     recipe and every recipe maps to a registered payload — a payload
     without a recipe fails the suite, so the gate cannot silently fall
     behind the registry (the wave-6 lesson applied to the gate itself).

4. **The wave-17 finding is fixed as data, not just as code:**
   `geointproof.json` was regenerated (routing@1.2.0) and is committed
   in the same wave as the gate.

## Consequences

- Changing any module whose version or behavior flows into a stamped
  payload now turns the suite red until the payload is regenerated and
  committed. This is the intended friction: evidence that lags its
  machinery is a defect.
- Mid-wave development is not blocked: new payloads are checked against
  their pre-run working-tree bytes until their first commit.
- The census discipline extends: this wave's census (29 payloads, 22
  exporters, both families) found 28 fresh, 1 stale — and the stale one
  is named in the freshproof payload itself.

## Alternatives considered

- **Compare exporter stdout to committed files for all exporters.** The
  hash family prints digests, not content; forcing byte output would
  churn five exporters for no doctrine gain. Digest comparison is
  freshness at the same strength.
- **Run the freshness check inside proofindex.verify_all via
  subprocess.** Rejected: a stdlib library spawning processes is a
  supply-chain and portability regression; injection keeps the seam
  honest and the demo byte-deterministic.
- **Treat stale as amber.** Rejected: stale evidence presented as
  current is a red-class integrity event — the stamp is green while the
  claim is false.
