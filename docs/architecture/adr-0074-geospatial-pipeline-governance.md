# ADR-0074 — Geospatial pipeline governance: the estate's GIS packages adopt the CRS layer

- Status: accepted
- Date: 2026-11-11
- Wave: 13 (README row 212)

## Context

ADR-0073 shipped geolock — CRSs as data, projections pinned by
known-answer vectors, coordinates under digest contract. But the two
packages that actually move geography through the estate predated it
and still behaved as if coordinates were self-describing:

1. **gisexport** emitted valid RFC 7946 GeoJSON — but never *said* so.
   No CRS declaration, no digest, and its density centroids were
   degree-averages: folklore arithmetic, silent.
2. **routing** was worse: `build_graph_from_geojson` range-checked
   nothing. A metre-space extract (Web Mercator, UTM — the natural
   output of a GDAL workbench session) sailed through and produced a
   nonsense graph. The CRS gate was fail-open.
3. The geodesy model (spherical haversine, mean radius) was implicit
   in the code, never stated as data.

Wave 12's own honesty block said "everything silently assumed WGS-84
degrees." A seam that exists but is not adopted by its own estate is
decoration. This wave is the adoption.

## Decision

1. **Every export states its CRS contract.** gisexport collections
   carry an `openhealth:geolock` foreign member (RFC 7946 §6.1 permits
   foreign members; §4 forbids a `crs` member): the CRS, the axis
   order, a features digest, and whether the export was governed.
   Ungoverned exports state `rfc-7946-default-stated` — disclosure,
   not silence.
2. **Governed exports fail closed.** With a geolock CRSRegistry
   injected, the export CRS must be registered and geographic; an
   unregistered or projected CRS is a named refusal.
3. **Centroids in metres when governed, disclosed in degrees when
   not.** Governed density layers project each cell's points to their
   UTM zone (the zone follows the coordinate), average eastings and
   northings, and transform back; the feature carries
   `centroid_space: utm-metres`. Ungoverned layers carry
   `centroid_space: degree-average`. Folklore is named, never hidden.
4. **The router's CRS gate is fail-closed.** Every coordinate is
   range-validated at graph build with the offending coordinate
   named; declared CRSs (legacy `crs` member or geolock foreign
   member) must be WGS-84 lng/lat or the build refuses; undeclared
   extracts build with basis `assumed-rfc-7946` — stated on the
   graph. An injected registry upgrades the basis to
   `registry-governed`.
5. **Injection, not import.** routing never imports geolock; the
   registry is injected like a clock (the shipgate/proofindex
   doctrine). gisexport imports geolock for the projection math —
   transforming is geolock's job, validating is the consumer's.
6. **Digests extend to routes.** Graphs pin their source extract;
   routes pin endpoints and result. The geodesy model is stated as
   data (`GEODESY_CONTRACT`): spherical haversine, ≤0.3% vs WGS-84,
   never presented as survey-grade.

## Consequences

- Existing callers are untouched: every new parameter is keyword-only
  with a legacy default, and the legacy path now *discloses* what it
  used to hide.
- `visitplan` keeps its documented haversine × road-factor heuristic;
  its contract is stated in its docstring and inherits
  `GEODESY_CONTRACT` semantics by reference.
- The geointproof demo surface exercises the full governed pipeline
  end to end — preflight, governed export, metre centroids,
  CRS-gated graph build, digested route — plus six attacks: four fail
  closed, two pass with the basis stated.
- proofindex registers geointproof (24 payloads: compact ×16,
  spaced-utf8 ×8); the meta gate now covers 17 exporters.

## Alternatives considered

- **Fix routing only, leave gisexport** — rejected: the silent
  degree-average centroid is exactly the folklore geolock exists to
  refuse.
- **Hard geolock import in routing too** — rejected: routing needs
  validation, not projection; injection keeps the seam one-way and
  testable with a stub registry.
- **Rewrite centroids unconditionally to metres** — rejected:
  ungoverned callers have no registry and no zone; refusal would
  break the offline-first export path. Disclosure is the honest
  middle; governance upgrades it.
