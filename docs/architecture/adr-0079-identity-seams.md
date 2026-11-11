# ADR-0079: Injectable identity seams — determinism walks the real flow

- Status: accepted (2026-11-11, wave 18)
- Supersedes: nothing; extends ADR-0055 (qrtrust) and the wave-5 meta-gate doctrine

## Context

Wave 5's exporter-determinism meta gate caught a real defect: vhsupport minted
ticket IDs with `secrets.token_hex(4)` per seed run, so the committed
`vhc-helpdesk.json` was irreproducible. The fix was an injectable `id_fn`
seam — production keeps secrets-unique IDs, demos inject determinism.

That fixed one package. Wave 18's census swept the estate for the same
pattern (scope stated: python `packages/`, grep for `secrets.token_*` /
`secrets.choice` / `random.*` mint patterns in non-test, non-demo code):

- **25 mint sites found.** 23 had no seam. Every demo needing determinism had
  to *sidestep the real flow* — `demo_tamper.py` builds its token via
  `sign_claims` because `purchase()` could not be replayed byte-identically.
  A gate the demos route around is a gate that is not watching the real path.
- **One site refused a seam:** `ips/shl.py`'s SHL key mint
  (`secrets.token_bytes(32)`). A deterministic key generator mints forged
  credentials; the seam there is a loaded gun, so the refusal is stated as
  data on the proof surface.
- **One site pre-existing:** `vhsupport` — the pattern source.
- **Out of scope, stated:** JS/Go services do not mint these engine IDs; the
  census does not pretend to cover them (the wave-17 lesson: a census states
  its runtimes and method).

Also closed this wave: the one observed flake of
`test_tampered_qr_is_red_not_a_crash` (stated in wave 17 as "watch it").
25/25 reproduction runs green; the test now pins its tamper flip provably
inside the signature segment (`rindex(".")`), removing the last theoretical
nondeterminism. The flake stays on record — stated, not forgotten.

## Decision

1. **Every mint site takes an injectable identity source.** Registry classes
   gain `id_fn=None` constructor parameters; module-level functions gain
   keyword-only `id_fn=None`; surveillance's Laplace DP noise gains a
   `seed` field (seeded `random.Random` vs the module `random`). Defaults are
   byte-for-byte the old behavior — secrets-unique in production, always.
2. **The difference is a constructor argument, never a branch on
   environment.** Callers inject; the code never sniffs "am I in a demo".
3. **`ips/shl.py` is refused a seam**, with the reason stated on the proof
   surface and in the census.
4. **The proof surface walks the REAL flow.** `idseamproof`'s control runs
   quote → purchase → signed QR twice under an injected counter and pins the
   two runs byte-identical; a seam sweep mints once per site via the real
   entry points (10 sites, prefixes intact); production uniqueness is
   asserted at export time with only booleans reaching the payload (pinning
   random values would pin randomness).
5. Same-wave discipline: deterministic stamped surface + README row +
   proofindex registration (29 payloads) + meta-gate exporter (22) in the
   same commit.

## Consequences

- Demos and reproducibility harnesses can now exercise purchase, claims,
  rescue, incidents, notifications, settlement, group sales, passes, and DP
  noise **through the real entry points** and still be byte-stable.
- The meta gate now watches the paths operators actually run, not parallel
  constructions.
- Future mint sites without a seam are a stated defect class: the census is
  data, and the pattern is one constructor argument.

## Alternatives considered

- **Module-level seam globals** — rejected: process-global mutable state is
  the test-hygiene defect class (snapshot/restore lesson); constructor
  injection composes.
- **Seam the SHL key mint too "for symmetry"** — refused: a deterministic
  key seam is a forgery kit; symmetry is not worth a loaded gun.
- **Leave sidestepped demos as-is** — rejected: evidence about a sidestep is
  evidence about nothing.
