# ADR-0076: The export gate — every GeoJSON artifact through the governed exporter

Status: accepted (2026-11-11, wave 15)
Extends: ADR-0074 (pipeline governance), ADR-0075 (geodesy convergence)

## Context

ADR-0074 built the governed export path; ADR-0075 converged the
arithmetic. The wave-15 recon asked the doctrine question — *is the seam
adopted by its own estate?* — and found two holes:

1. **One raw-GeoJSON site remained.** `households.to_geojson()`
   hand-built its FeatureCollection: no CRS member, no digest, no gate.
   It also rounded coordinates to 5 decimal places (~1.1 m) — a genuine
   privacy/precision decision — *without saying so anywhere*. A planner
   holding the file could not know what they were holding.
2. **The exporter's own seam was soft.** `gisexport.collection()` — the
   public wrapper every caller-built feature passes through — stamped
   features without validating their coordinates. The in-package layer
   helpers (`_feature`) range-checked their own points, but a caller
   hand-building a feature (exactly what households did) could smuggle a
   metre-space coordinate through `collection()` and receive a
   governed-looking, digest-stamped artifact. The checksum faithfully
   pinned garbage. *The checksum is the contract* presumes bytes that
   deserve it.

No other export path carries coordinates: the apps JS builds no GeoJSON,
the CSV writers (graphspike lakefeed, catchment loader) move node/edge
IDs and read coordinates respectively — never write them.

## Decisions

1. **The export gate fails closed.** `gisexport@1.2.0`: `collection()`
   range-validates every position in every feature BEFORE stamping,
   recursively, with the offending coordinate and feature index named.
   Null geometries are legal RFC 7946 (§3.2) and yield nothing. The
   member states `coordinates_checked` — what was verified is data.
2. **Geometry types are a closed vocabulary.** Point, MultiPoint,
   LineString, MultiLineString, Polygon, MultiPolygon. GeometryCollection
   is refused until a caller needs it — an ungated geometry is an
   unstamped one.
3. **households adopts the governed exporter.** `to_geojson()` delegates
   assembly to `gisexport.collection()`, gains a keyword-only `registry`
   (backward compatible — both legacy tests untouched), and attaches its
   own foreign member: `anonymized`, `coordinate_quantization` (5dp ≈
   1.1 m, planning-map precision, stated), `as_of`. Privacy choices are
   data — an identified export says `anonymized: false` on its face.
4. **Defence in depth, named at both walls.** `households.register()`
   already range-checked at the door but with a bare "invalid
   coordinates"; it now names the offending lat/lon. Registration is the
   first wall; the export gate is the second, and it protects every
   caller-built feature, not just this package's.
5. **The census is data.** The demo surface carries the estate grep
   result: raw-GeoJSON sites 1 → 0. A seam unadopted by its own estate
   is decoration; the adoption is now provable.

## Consequences

- Behavior change, stated: `collection()` now refuses out-of-range
  coordinates in caller-built features (gisexport.Error) and unknown
  geometry types. All in-estate callers pass valid WGS-84 degrees — full
  suite green.
- Wave-13 doctrine generalized: the router gate (ADR-0074) and the
  export gate (this ADR) are the same wall at two borders — ingest and
  egress. Fail-closed at both.
- Demo surface, same wave: `geoexportproof-demo@1.0.0` →
  `apps/pwa/geoexportproof.json` + board; proofindex payload #26;
  meta-gate exporter #19.

## Alternatives considered

- **Validate only in governed mode (registry injected)**: the hole is
  independent of CRS governance — a digest over garbage is garbage in
  either mode. The gate runs always.
- **Snap/Clamp out-of-range coordinates instead of refusing**: silently
  moving a coordinate is worse than refusing it — a clamped metre-space
  easting becomes a plausible-looking wrong degree. Refuse, name, let
  the caller reproject (geolock.transform).
- **Leave households' hand-built export, add the member manually**:
  duplicates the exporter's assembly and drifts — the doctrine answer
  to a seam is adoption, not imitation.
