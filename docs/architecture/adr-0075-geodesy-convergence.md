# ADR-0075: Geodesy convergence — one contract, one function, six adoptions

Status: accepted (2026-11-11, wave 14)
Supersedes: nothing; extends ADR-0074 (geospatial pipeline governance)

## Context

ADR-0074 governed *coordinates*: CRS as data, fail-closed gates, stated
bases. It deliberately left *distance arithmetic* for a later wave. The
recon for this wave found the estate measured distance **five different
ways**:

| site | radius | basis stated? |
|---|---|---|
| `households.haversine_km` | 6371.0 | no |
| `surveillance/outbreak._km` | 6371.0 | no |
| `routing/visitplan.haversine_km` | 6371.0 | heuristic documented, contract not stated |
| `kpiplane._haversine_km` | **6371.0088** (IUGG mean) | no |
| `openhealth_ml/catchment.haversine_km` | **6371.0088** (IUGG mean) | no |

Two different earth radii in one estate means the same pair of
coordinates yields different distances in different subsystems — catchment
coverage and catchment analytics could disagree. Every implementation was
also one float-rounding hair away from a domain error at exactly
antipodal inputs (`asin(sqrt(a))` with `a` marginally above 1). This is
the same defect ADR-0074 named for CRS: silent folklore, re-implemented
per package.

## Decisions

1. **geolock owns geodesy.** `geolock.GEODESY_CONTRACT` (spherical
   haversine, r = 6371.0 km, honest error ≤0.3% vs WGS-84 geodesic, never
   survey-grade) is stated as data, and `geolock.distance_km` is the one
   function. geolock@1.1.0.
2. **The retired IUGG mean is disclosed, not silently swapped.** The
   divergence between 6371.0088 and 6371.0 is 1.381 ppm — more than
   2000x narrower than the contract's stated 0.3% (3000 ppm) honesty
   band, so the correction moves no planning decision and no stamped
   surface. kpiplane carries `GEODESY_SUPERSEDED` as data; the demo
   quantifies the divergence on a fixed reference leg.
3. **All five private haversines delegate.** households, kpiplane,
   outbreak, visitplan and catchment keep their historic public names
   (callers and tests untouched) and delegate to `geolock.distance_km`.
   A convergence matrix in the demo surface proves the six sites
   bit-identical on five fixed reference legs; if any site is ever
   reverted to a private copy, the matrix diverges and the meta gate
   fails.
4. **The clamp is stated.** `distance_km` caps the haversine argument at
   1.0 — value-identical everywhere the retired implementations did not
   crash, and defined at the antipodes where they would raise. The demo
   carries a true-antipodal leg (20,015.087 km, half the contract
   circumference) as evidence.
5. **ADR-0074 decision 5 is refined, not reversed.** The *router*
   (`routing.py`) still never imports geolock — its registry is injected
   like a clock, and it keeps its own stated `GEODESY_CONTRACT`.
   `visitplan.py` imports only a pure function and a constant: that is
   arithmetic, not an engine. The line the estate holds is **registry =
   injection; arithmetic = shared**.
6. **Duplication with a drift guard is four-eyes.** Because routing.py
   keeps its own contract by design, packages/geodesy pins the semantic
   core (`model`, `radius_km`, `honest_error`) of both contracts equal
   in the suite. A radius edit in either file fails the gate instead of
   drifting silently.
7. **Fail-closed reaches the function.** `distance_km` range-checks all
   four inputs and refuses metre-space coordinates with the offender
   named — the wave-13 router gate pushed down into the arithmetic, so
   even a caller that skips the router cannot silently measure a
   projected extract.

## Consequences

- Every coverage, catchment-assignment, cluster and visit-plan result
  now carries its geodesy basis as data (`geodesy` member: basis +
  contract, plus `superseded` where a radius retired). Absence is not a
  value — the arithmetic a planning decision rests on is disclosed.
- Behavior change, stated: out-of-range coordinates into the five
  delegating callables now refuse (GeolockError) instead of silently
  returning nonsense. No caller in the estate relied on the old silent
  behavior (full suite green, 3,251 passed + 18 subtests).
- Demo surface: `geodesyproof-demo@1.0.0` → `apps/pwa/geodesyproof.json`
  + board, proofindex payload #25, meta-gate exporter #18 — same wave,
  per doctrine.

## Alternatives considered

- **Bump the contract to the IUGG mean (6371.0088)**: marginally more
  accurate, but it would re-stamp every surface pinned on 6371.0
  (routing edge weights, geointproof route digests) for a 1.381 ppm
  correction the stated honesty band already absorbs. The checksum is
  the contract; we converge on the pinned one.
- **Leave the five private haversines, document them**: that is the
  folklore this ADR exists to kill — documentation does not make five
  code paths agree.
- **A shared `geodesy.py` utility module outside geolock**: a seventh
  package to own one function fragments authority; geolock already owns
  coordinate governance, and distance between governed points is
  coordinate math.
