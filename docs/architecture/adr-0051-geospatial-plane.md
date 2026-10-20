# ADR-0051: Geospatial plane — PostGIS registry + capacity-aware catchments + coverage gaps

Status: accepted (2026-10-20)

## Context

Geospatial before this ADR: haversine KNN in Go over an in-memory
facility list with a bounding-box B-tree prefilter (ADR-0014), an
O(members × facilities) catchment pass in kpiplane, and NN+2-opt CHW
routing. Correct — but four gaps kept it from world-class:

1. **No real spatial index.** Full-scan haversine stops being a plan
   past ~50k facilities; the national registry target is far larger.
2. **Nearest ≠ right.** Pure distance assignment overloads the closest
   PHC while its neighbour sits empty — the referral-steering failure.
3. **Distance ≠ time.** A straight-line km on a Lagos arterial and on a
   rural track are different journeys; ">15km" alone misstates access.
4. **No equity view.** Catchments say how loaded facilities are; nothing
   said who the network *forgets entirely*.

## Decision

**PostGIS in the platform Postgres** (migration 0016; compose image
`postgis/postgis:16-3.4-alpine`, an API-compatible drop-in):

- `geography(Point,4326)` column maintained by a database **trigger** —
  writers keep inserting plain lat/lng doubles; no dual-write drift, no
  service code changes.
- GiST index; `registry-service` answers `/facilities/near` via
  ST_DWithin + KNN (`<->`) when PostGIS is present, and **feature-
  detects** its absence to fall back to in-memory haversine — the JSON
  contract is unchanged and responses carry an `engine` field so ops
  can see which path answered.
- Licence note: PostGIS is GPL-2.0 but runs server-side inside Postgres;
  nothing is linked into our binaries (same doctrine as any permissive
  stack using it over the wire protocol). Our services stay stdlib Go.

**Geo intelligence in kpiplane** (all deterministic, stdlib Python):

- `travel_minutes` — walk under 2 km (4.5 km/h WHO access standard),
  else motorised at road-class speeds over road-factored distance
  (urban 22 / periurban 35 / rural 28 km/h × 1.4).
- `assign_catchments_capacity` — nearest facility **with headroom**;
  capacity proxy beds × monthly outpatient equivalents; overflow spills
  to next-nearest and is counted; rows gain `capacity`,
  `saturation_pct`, `mean_travel_min`. The old function remains for
  contract compatibility.
- `coverage_gaps` — members whose *nearest* facility exceeds distance
  OR travel-time thresholds, grouped by LGA with clusterable points:
  siting guidance, not just load stats.

**Surfaces:** the dashboards' catchments table gains Load (saturation)
and Mean travel columns; a Coverage-gaps panel ranks LGAs by unserved
members with the siting recommendation spelled out.

## Boundaries (unchanged, restated)

Apache Sedona/Spark remains the swap point for national-scale batch
spatial joins (the kpiplane function contract is the seam). The
read path stays off WebGL/tile servers — $100 phones on 2G get the
self-contained canvas map. PostGIS serves the registry plane, not edge
nodes.

## Consequences

- Platform Postgres image changes (postgis variant); existing volumes
  migrate forward via the trigger backfill.
- kpiplane output gains `coverage_gaps` section and richer catchment
  rows — additive only; existing consumers keep working.
- Every new piece is tested: travel-time model, overflow spill,
  saturation math, gap detection, point capping, plus the full
  registry-service suite against the fallback path.
