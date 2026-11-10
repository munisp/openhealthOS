"""GeoJSON export for offline GIS planning (the RustGIS/any-QGIS companion).

The platform stays boring at runtime; spatial *analysis* (where to build the
next PHC, which catchments are underserved, ambulance response-time heat)
happens offline in a GIS workbench. This package is the bridge: it emits
standards-compliant RFC 7946 GeoJSON FeatureCollections that RustGIS, QGIS,
or any GIS tool opens directly.

All exports are k-anonymised by default for patient-derived layers — the
same doctrine as surveillance (ADR-0020): planners need density, not names.

gisexport@1.1.0 (ADR-0074) — the CRS layer is no longer silent:

- Every collection carries an ``openhealth:geolock`` foreign member
  (RFC 7946 §6.1 permits foreign members; §4 forbids a ``crs`` member, so
  the contract travels under our own name). The member states the CRS,
  the axis order, and whether the export was governed by a geolock
  CRSRegistry. ``governed: false`` means the RFC 7946 default (WGS-84,
  lng/lat) is *stated*, not assumed — silence was the defect.
- With a registry injected, the CRS must be registered and geographic —
  an unregistered CRS is a named refusal, never a fallback.
- Density centroids: without a registry the centroid is a degree-average
  and the feature SAYS SO (``centroid_space: degree-average``); with a
  registry the cell's points are projected to their UTM zone, averaged
  in metres, and transformed back (``centroid_space: utm-metres``).
  Folklore is named, never hidden.

gisexport@1.2.0 (ADR-0076) — the export gate: ``collection()`` range-
validates every coordinate in every feature BEFORE stamping, over a
closed geometry vocabulary, with the offender named. The checksum is
the contract — and the checksum presumes bytes that deserve it. The
member states how many positions were checked.

gisexport@1.3.0 (ADR-0077) — the structure gate, and the wave-14
census miss closed:

- ``collection()`` now validates RFC 7946 STRUCTURE before range: a
  Point carries exactly one position (a Point wearing a position list
  is refused — that shape is a MultiPoint); a LineString needs at
  least two positions; every polygon ring has at least four positions
  and is CLOSED (first position == last, §3.1.6); empty coordinates
  are refused — an empty geometry is a null geometry, and null is how
  you say so (§3.2); positions are two or three numbers (the third is
  elevation, counted and disclosed — beyond elevation the estate has
  no contract); a feature or geometry ``bbox`` must be well-formed
  [minLng, minLat, maxLng, maxLat] and must CONTAIN its own geometry —
  a bbox that contradicts its positions is a lie with a checksum.
  Antimeridian-crossing bboxes (minLng > maxLng, §5.2) are outside the
  export contract and refused with the reason named — the estate's
  theatres are West-African; if that changes, the gate changes loudly.
- The member carries ``structure_checked`` (geometries / positions /
  rings / elevations / null geometries) alongside
  ``coordinates_checked``. An in-range but malformed geometry used to
  be stamped; now shape is gated before bytes are pinned.
- THE CENSUS MISS: wave 14 (ADR-0075) retired five private haversines
  and missed a SIXTH — the closure inside ``coverage_gap_layer``
  below, r=6371.0, undocumented by the convergence matrix. It now
  delegates to ``geolock.distance_km`` (exposed here as
  ``gisexport.distance_km`` — the gate package computes on the same
  contract it stamps), and the layer's collection carries an
  ``openhealth:geodesy`` member naming the basis and the miss. A
  census is data; a census that missed is stated, not quietly fixed.
"""

import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "geolock"))

import geolock  # noqa: E402

GIS_VERSION = "gisexport@1.3.0"

K_ANONYMITY = 5  # suppress cells below this count

#: The CRS contract every export states. GeoJSON is WGS-84 with
#: longitude-first axis order (OGC CRS84); geolock's EPSG:4326 entry is
#: the same ellipsoid under the estate's x=lng, y=lat convention.
CRS_CONTRACT = {
    "code": "EPSG:4326",
    "axis_order": "lng,lat",
    "spec": "RFC 7946 (WGS-84, OGC CRS84 axis order)",
}


class Error(Exception):
    pass


def _canon(obj) -> str:
    """Compact canonical digest — the estate's primary contract."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _geolock_member(registry, features):
    """The foreign member every collection carries. Governed mode fails
    closed: the CRS must be registered and geographic."""
    member = {"crs": CRS_CONTRACT["code"],
              "axis_order": CRS_CONTRACT["axis_order"],
              "features_sha256": _canon(features)}
    if registry is None:
        member["governed"] = False
        member["basis"] = "rfc-7946-default-stated"
        return member
    rec = registry.get(CRS_CONTRACT["code"])  # named refusal if absent
    if rec.get("kind") != "geographic":
        raise geolock.GeolockError(
            "export CRS %r is %s — GeoJSON is geographic; project in "
            "the workbench, not at the gate"
            % (CRS_CONTRACT["code"], rec.get("kind")))
    member["governed"] = True
    member["basis"] = "registry-governed"
    return member


def _feature(lng, lat, properties):
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise Error(f"coordinate out of range: {lng},{lat}")
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": properties}


#: Closed geometry vocabulary for the export gate (ADR-0076).
#: GeometryCollection is refused until a caller needs it — a nested
#: geometry nobody uses is a gate nobody tested.
GEOMETRY_TYPES = ("Point", "MultiPoint", "LineString",
                  "MultiLineString", "Polygon", "MultiPolygon")


def _is_position(node):
    """A position is two or three numbers [lng, lat] or
    [lng, lat, elevation] (RFC 7946 §3.1.1). Booleans are not numbers
    here; beyond elevation the estate has no contract."""
    return (isinstance(node, (list, tuple)) and 2 <= len(node) <= 3
            and all(isinstance(v, (int, float))
                    and not isinstance(v, bool) for v in node))


def _structure(geometry, where, stats):
    """Fail-closed structural walker (ADR-0077): validate RFC 7946
    shape and yield (lng, lat) positions. Structure is gated BEFORE
    range and BEFORE stamping — an in-range but malformed geometry is
    still a malformed geometry, and the checksum presumes bytes that
    deserve it. A null geometry is legal (§3.2) and yields nothing —
    absence is stated, not validated."""
    if geometry is None:
        return
    gtype = geometry.get("type")
    if gtype not in GEOMETRY_TYPES:
        raise Error(
            "geometry type %r at %s is outside the closed export "
            "vocabulary %s — an ungated geometry is an unstamped one"
            % (gtype, where, GEOMETRY_TYPES))
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) == 0:
        raise Error(
            "geometry %r at %s has empty or missing coordinates — an "
            "empty geometry is a null geometry; use null and say so "
            "(RFC 7946 §3.2)" % (gtype, where))

    def position(p, w):
        if not _is_position(p):
            raise Error(
                "malformed position at %s: %r — a position is two or "
                "three numbers [lng, lat] or [lng, lat, elevation]; "
                "beyond elevation the estate has no contract" % (w, p))
        stats["positions"] += 1
        if len(p) == 3:
            stats["elevations"] += 1
        return p[0], p[1]

    def ring(r, w):
        if not isinstance(r, (list, tuple)) or len(r) < 4:
            raise Error(
                "linear ring at %s has %s positions — RFC 7946 §3.1.6 "
                "requires at least four and a closed ring"
                % (w, len(r) if isinstance(r, (list, tuple)) else r))
        for p in r:
            if not _is_position(p):
                raise Error("malformed position at %s in ring: %r"
                            % (w, p))
        if list(r[0]) != list(r[-1]):
            raise Error(
                "linear ring at %s is not closed — first position %r "
                "!= last %r (RFC 7946 §3.1.6); an unclosed ring is "
                "not a polygon" % (w, list(r[0]), list(r[-1])))
        stats["rings"] += 1
        for p in r:
            yield position(p, w)

    stats["geometries"] += 1
    if gtype == "Point":
        if not _is_position(coords):
            raise Error(
                "Point at %s wears %r as coordinates — a Point carries "
                "exactly one position; a list of positions is a "
                "MultiPoint, and a smuggled shape is a refused shape"
                % (where, coords))
        yield position(coords, where)
    elif gtype == "MultiPoint":
        for p in coords:
            yield position(p, where)
    elif gtype == "LineString":
        if len(coords) < 2:
            raise Error(
                "LineString at %s has one position — a line needs at "
                "least two (RFC 7946 §3.1.4)" % where)
        for p in coords:
            yield position(p, where)
    elif gtype == "MultiLineString":
        for line in coords:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                raise Error(
                    "MultiLineString part at %s has %s positions — "
                    "every part is a LineString and needs at least two "
                    "(RFC 7946 §3.1.5)"
                    % (where,
                       len(line) if isinstance(line, (list, tuple))
                       else line))
            for p in line:
                yield position(p, where)
    elif gtype == "Polygon":
        for r in coords:
            yield from ring(r, where)
    else:  # MultiPolygon
        for poly in coords:
            if not isinstance(poly, (list, tuple)) or len(poly) == 0:
                raise Error(
                    "MultiPolygon part at %s has no rings — every part "
                    "is a Polygon (RFC 7946 §3.1.7)" % where)
            for r in poly:
                yield from ring(r, where)


def _positions(geometry, where):
    """Yield (lng, lat) positions from a geometry, structure-validated
    (ADR-0077) over the closed vocabulary (ADR-0076)."""
    stats = {"geometries": 0, "positions": 0, "rings": 0,
             "elevations": 0}
    yield from _structure(geometry, where, stats)


def _check_bbox(bbox, where, pts):
    """A bbox, when present, must be well-formed and must CONTAIN its
    own geometry (RFC 7946 §5). Antimeridian-crossing bboxes (minLng >
    maxLng, §5.2) are refused with the reason named — outside the
    export contract for the estate's theatres."""
    if bbox is None:
        return
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4
            and all(isinstance(v, (int, float))
                    and not isinstance(v, bool) for v in bbox)):
        raise Error(
            "bbox at %s is %r — exports are 2-D; a bbox is [minLng, "
            "minLat, maxLng, maxLat] (RFC 7946 §5)" % (where, bbox))
    if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
        raise Error(
            "bbox at %s has min after max: %r — antimeridian-crossing "
            "bboxes are outside the export contract; split the "
            "geometry instead" % (where, list(bbox)))
    for lng, lat in pts:
        if not (bbox[0] <= lng <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            raise Error(
                "bbox at %s does not contain its own geometry — %s,%s "
                "lies outside %r; a bbox that contradicts its "
                "positions is a lie with a checksum"
                % (where, lng, lat, list(bbox)))


def _check_structure(features):
    """Fail-closed structural gate (ADR-0077): shape before range
    before stamp. Returns the counts the member discloses."""
    stats = {"geometries": 0, "positions": 0, "rings": 0,
             "elevations": 0, "null_geometries": 0}
    for i, feat in enumerate(features):
        where = "feature %d" % i
        geom = feat.get("geometry")
        if geom is None:
            stats["null_geometries"] += 1
            pts = []
        else:
            pts = list(_structure(geom, where, stats))
        _check_bbox(feat.get("bbox"), where, pts)
        if geom is not None:
            _check_bbox(geom.get("bbox"), where + " geometry", pts)
    return stats


def _check_coordinates(features):
    """Fail-closed coordinate gate (ADR-0076): every position in every
    feature is range-validated BEFORE the collection is stamped. The
    checksum is the contract — and the checksum presumes bytes that
    deserve it. A metre-space extract used to sail through collection()
    and get a governed-looking stamp; now the offender is named.
    Returns the number of positions checked (stated on the member)."""
    n = 0
    for i, feat in enumerate(features):
        for lng, lat in _positions(feat.get("geometry"),
                                   "feature %d" % i):
            if not (-180 <= lng <= 180 and -90 <= lat <= 90):
                raise Error(
                    "coordinate out of range at feature %d: %s,%s — "
                    "metre-space coordinates are not degrees; reproject "
                    "first (geolock.transform)" % (i, lng, lat))
            n += 1
    return n


def distance_km(lat1, lng1, lat2, lng2):
    """The sixth delegation (ADR-0077): coverage_gap_layer's private
    closure haversine — the wave-14 census miss — is retired. The gate
    package computes on the same contract it stamps."""
    return geolock.distance_km(lat1, lng1, lat2, lng2)


def collection(features, *, registry=None):
    structure = _check_structure(features)
    checked = _check_coordinates(features)
    fc = {"type": "FeatureCollection", "features": features}
    fc["openhealth:geolock"] = _geolock_member(registry, features)
    fc["openhealth:geolock"]["structure_checked"] = structure
    fc["openhealth:geolock"]["coordinates_checked"] = checked
    return fc


def facilities_layer(facilities, *, registry=None):
    """facilities: [{id, name, kind, lat, lng, beds, services:[...]}]"""
    feats = [_feature(f["lng"], f["lat"],
                      {"id": f["id"], "name": f["name"], "kind": f["kind"],
                       "beds": f.get("beds", 0),
                       "services": ";".join(f.get("services", []))})
             for f in facilities]
    return collection(feats, registry=registry)


def _metre_centroid(registry, lats, lngs):
    """Average in metres, not degrees: project every point to the UTM
    zone of the degree-average longitude, average eastings/northings,
    transform back. The zone follows the coordinate."""
    zone = geolock.utm_zone_for(sum(lngs) / len(lngs))
    code = "EPSG:326%02d" % zone
    registry.get(code)  # named refusal: the zone must be registered
    es, ns = [], []
    for lat, lng in zip(lats, lngs):
        r = geolock.transform(registry, CRS_CONTRACT["code"], code,
                              lng, lat)
        es.append(r["output"]["x"])
        ns.append(r["output"]["y"])
    back = geolock.transform(registry, code, CRS_CONTRACT["code"],
                             sum(es) / len(es), sum(ns) / len(ns))
    return back["output"]["y"], back["output"]["x"], code


def households_density_layer(households, cell_deg=0.05, k=K_ANONYMITY,
                             *, registry=None):
    """Aggregate households into grid cells (default ~5 km) with k-anonymity.
    households: [{lat, lng, members}] — raw points NEVER leave this layer.

    Centroid honesty (ADR-0074): governed exports average in UTM metres;
    ungoverned exports average in degrees and the feature says so."""
    cells = {}
    for h in households:
        key = (math.floor(h["lat"] / cell_deg), math.floor(h["lng"] / cell_deg))
        c = cells.setdefault(key, {"households": 0, "members": 0,
                                   "lats": [], "lngs": []})
        c["households"] += 1
        c["members"] += h.get("members", 1)
        c["lats"].append(h["lat"])
        c["lngs"].append(h["lng"])
    feats = []
    for (ilat, ilng), c in sorted(cells.items()):
        if c["households"] < k:
            continue  # k-anonymity: sparse cells are suppressed entirely
        if registry is None:
            clat = sum(c["lats"]) / len(c["lats"])
            clng = sum(c["lngs"]) / len(c["lngs"])
            space = "degree-average"
        else:
            clat, clng, zone_code = _metre_centroid(
                registry, c["lats"], c["lngs"])
            space = "utm-metres"
        props = {"households": c["households"], "members": c["members"],
                 "cell": f"{ilat}_{ilng}", "centroid_space": space}
        if registry is not None:
            props["centroid_crs"] = zone_code
        feats.append(_feature(clng, clat, props))
    return collection(feats, registry=registry)


def response_time_layer(incidents, cell_deg=0.05, k=K_ANONYMITY,
                        *, registry=None):
    """incidents: [{lat, lng, response_min}] -> mean response per cell,
    k-anonymised. This is the layer planners overlay on population density
    to site new ambulance posts. Cell centres are degree-grid coordinates —
    a deterministic grid, stated as ``cell_space``."""
    cells = {}
    for inc in incidents:
        key = (math.floor(inc["lat"] / cell_deg), math.floor(inc["lng"] / cell_deg))
        cells.setdefault(key, []).append(inc["response_min"])
    feats = []
    for (ilat, ilng), times in sorted(cells.items()):
        if len(times) < k:
            continue
        feats.append(_feature(
            (ilng + 0.5) * cell_deg, (ilat + 0.5) * cell_deg,
            {"incidents": len(times),
             "mean_response_min": round(sum(times) / len(times), 1),
             "worst_response_min": max(times),
             "cell": f"{ilat}_{ilng}", "cell_space": "degree-grid"}))
    return collection(feats, registry=registry)


def coverage_gap_layer(facilities, households, max_km=5.0, k=K_ANONYMITY,
                       *, registry=None):
    """Households farther than max_km from ANY facility — the classic
    'where is care out of reach' layer. k-anonymised grid output.
    Distances delegate to geolock.distance_km (ADR-0077 — this layer's
    private closure was the wave-14 census miss); output cells are
    degree-grid coordinates, stated as ``cell_space``."""
    far = []
    for h in households:
        nearest = min((distance_km(h["lat"], h["lng"],
                                   f["lat"], f["lng"])
                       for f in facilities), default=None)
        if nearest is None or nearest > max_km:
            far.append({**h, "distance_km": nearest})
    cells = {}
    for h in far:
        key = (math.floor(h["lat"] / 0.05), math.floor(h["lng"] / 0.05))
        cells.setdefault(key, []).append(h)
    feats = []
    for (ilat, ilng), hs in sorted(cells.items()):
        if len(hs) < k:
            continue
        feats.append(_feature(
            (ilng + 0.5) * 0.05, (ilat + 0.5) * 0.05,
            {"unserved_households": len(hs),
             "members": sum(h.get("members", 1) for h in hs),
             "mean_distance_km": round(
                 sum(h["distance_km"] for h in hs) / len(hs), 1),
             "cell_space": "degree-grid"}))
    fc = collection(feats, registry=registry)
    fc["openhealth:geodesy"] = {
        "basis": "geolock.GEODESY_CONTRACT via gisexport.distance_km — "
                 "the gate computes on the contract it stamps",
        "contract": geolock.GEODESY_CONTRACT,
        "supersedes": "private closure haversine r=6371.0 — the wave-14 "
                      "census miss, named not hidden (ADR-0077)"}
    return fc
