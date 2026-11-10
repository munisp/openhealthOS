# ADR-0077: The structure gate — shape before range before stamp, and the wave-14 census miss

- Status: accepted (2026-11-11)
- Supersedes: nothing. Extends ADR-0076 (the export gate) and corrects the census behind ADR-0075 (geodesy convergence).

## Context

ADR-0076 taught `gisexport.collection()` to range-validate every
coordinate of every feature before stamping it. Recon for this wave
asked the next doctrine question: **is range the whole of "bytes that
deserve it"?** It is not. Two defects surfaced:

1. **Range is not shape.** The wave-15 walker found positions wherever
   they nested and range-checked them — so an in-range but structurally
   invalid geometry was stamped faithfully:
   - a Polygon ring left unclosed (RFC 7946 §3.1.6 requires first ==
     last and at least four positions);
   - a LineString carrying a single position (§3.1.4 requires two);
   - a Point whose coordinates were a *list of positions* — a
     MultiPoint's shape smuggled under a Point's type;
   - empty `coordinates: []` (§3.2 says absence is expressed as a null
     geometry — emptiness is a malformed geometry, not an honest null);
   - a `bbox` member contradicting its own feature's positions (§5) —
     a lie with a checksum.

2. **The wave-14 census missed a sixth private haversine.** ADR-0075
   retired five private haversines and presented the convergence matrix
   as the estate's complete set. `gisexport.coverage_gap_layer` carried
   a sixth — a closure with r=6371.0, undocumented by the matrix, in the
   very package that stamps exports. The estate's doctrine is that a
   census is data; a census that missed is **stated**, not quietly
   fixed.

## Decision

1. **Structure is gated before range before stamp.**
   `gisexport._structure()` validates RFC 7946 shape per geometry type
   over the closed vocabulary and yields positions; `_check_structure`
   walks every feature, validates any feature- or geometry-level `bbox`
   (well-formed `[minLng, minLat, maxLng, maxLat]`, min ≤ max, and
   containing every position of its own geometry), and returns the
   counts. `collection()` runs structure → range → stamp, and the
   `openhealth:geolock` member carries `structure_checked`
   (geometries / positions / rings / elevations / null_geometries)
   alongside `coordinates_checked`. What was verified is data.

2. **Closed rules, stated edges.**
   - Ring closure compares the full first and last positions
     element-wise (an elevation mismatch is an unclosed ring).
   - Positions are two or three numbers; the third is elevation,
     counted and disclosed. A fourth element is refused — beyond
     elevation the estate has no contract.
   - Empty coordinates are refused with the reason named; null
     geometries remain legal and are counted as nulls.
   - Antimeridian-crossing bboxes (§5.2, minLng > maxLng) are refused
     with the reason named — outside the export contract for the
     estate's theatres. If a theatre crosses the antimeridian, the gate
     changes loudly, not silently.

3. **The census miss is closed and stated.** `coverage_gap_layer`
   delegates to `geolock.distance_km`, exposed as
   `gisexport.distance_km` — the gate package computes on the same
   contract it stamps. The layer's collections carry an
   `openhealth:geodesy` member naming the basis and the miss. The
   geodesy convergence matrix (geodesyproof-demo@1.1.0) grew to seven
   sites, and a fifth drill states the miss on the artifact itself.

4. **The one intentional copy stays intentional.** `routing.py` keeps
   its own haversine and stated `GEODESY_CONTRACT` (ADR-0074: the router
   never imports geolock); `packages/geodesy`'s drift guard pins the
   semantic cores equal or the suite fails. Duplication with a guard is
   four-eyes; duplication without one is folklore. gisexport's closure
   was neither guarded nor intentional — that is the difference between
   a design and a miss.

## Consequences

- Every stamped GeoJSON artifact is now structure-checked, range-checked
  and digest-pinned, in that order.
- The convergence story is honest about its own history: the estate
  claimed completeness, was wrong by one, and says so on the proof
  surface where the claim lives.
- Consumers of the `openhealth:geolock` member gain `structure_checked`;
  `coordinates_checked` is unchanged.

## Alternatives considered

- **Validate structure in the GIS workbench instead.** Refused: the
  estate stamps artifacts; the stamp must presume deserving bytes, not
  defer the presumption downstream.
- **Quietly fix the census miss.** Refused by doctrine. A registry that
  falls behind silently is a blind spot; a census that is quietly
  corrected teaches the next census to be careless.
