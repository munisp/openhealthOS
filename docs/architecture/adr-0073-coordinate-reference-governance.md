# ADR-0073 — Coordinate-reference governance: CRSs as data, projections pinned by known answers

- Status: accepted
- Date: 2026-11-11
- Wave: 12 (README row 211)

## Context

The estate exports GeoJSON (gisexport, RFC 7946, k-anonymised) and
routes on OSM graphs (routing, A*). Everything silently assumed WGS-84
degrees: no CRS registry, no projection, no area measurement. Degrees
are not distances — a catchment sized in degrees is a planning error
with a budget attached.

Heavy geospatial I/O (GDAL/PROJ; pure-Rust ports like opengeos/terrio,
MIT) solves this at engine scale. The question was whether the
platform embeds one. It does not:

1. ADR-0005 — packages are stdlib-only Python; a Rust engine or the
   GDAL C stack cannot be embedded in the package estate.
2. terrio is MIT (embed-clean) — the refusal is architectural, not
   licensing.
3. What the estate needs is the governed core (CRS registry,
   projection math, coordinate contracts, area gates) plus a SEAM for
   heavy engines — the same adapter pattern as ledgeranchor notaries
   (ADR-0069), vhpsp PSP exports, and kgraph SPARQL endpoints
   (ADR-0072).

## Decision

1. **A CRS is a registry entry, never a code branch.** WGS-84 and Web
   Mercator ship seeded; UTM zones are deployment-registered. The
   projection vocabulary is closed (longlat / web-mercator / utm);
   anything heavier composes through adapters.
2. **A coordinate without a CRS is folklore.** Point records carry
   CRS + digest; ranges are validated at the door; transforms between
   unregistered CRSs refuse, named.
3. **Area in degrees is refused.** area_km2 demands a projected CRS.
4. **Known answers are data.** Every projection is pinned by control
   vectors verified against PROJ 3.7.2 at pin time, tolerance stated
   (1 cm). A formula that drifts from its own pinned vectors fails
   loudly — terrio's doctrine ("tested against GDAL/PROJ") applied to
   a stdlib core.
5. **Drift is a verdict, never silent.** verify_roundtrip returns
   green within the stated tolerance (1 mm) and red with the drift
   named beyond it.
6. **Heavy engines fail closed.** terrio-cli and gdal-cli are
   template adapters: two distinct named approvers ratify, an injected
   executor carries the wire, unstamped results are refused, a missing
   or failing wire is stated NOT RUN — never faked.

## Consequences

- Deployments register the UTM zone(s) their country spans as data —
  country-agnostic doctrine extends to coordinate systems.
- gisexport outputs can now be reprojected under contract before
  export; routing can snap to projected grids where the geography
  demands it.
- A deployment adopting terrio (COG windows, PMTiles, point clouds,
  full EPSG) ratifies the adapter and injects the executor — a
  registry entry, never a code branch.
- The proof surface (geoproof) exercises six pinned control vectors
  and eight attacks; the payload is stamped, registered in proofindex
  (23 payloads), and gated by the meta exporter-determinism suite (16
  exporters).

## Alternatives considered

- **Embed PROJ/terrio**: refused — ADR-0005 and deployment weight;
  the governed seam covers the platform's needs and the adapter
  covers the long tail.
- **pyproj as a dependency**: refused inside packages (ADR-0005);
  used only at pin time to generate the known-answer vectors, which
  are then data.
