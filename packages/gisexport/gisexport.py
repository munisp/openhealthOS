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
"""

import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "geolock"))

import geolock  # noqa: E402

GIS_VERSION = "gisexport@1.1.0"

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


def collection(features, *, registry=None):
    fc = {"type": "FeatureCollection", "features": features}
    fc["openhealth:geolock"] = _geolock_member(registry, features)
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
    Distances are spherical haversine (stated in routing.GEODESY_CONTRACT);
    output cells are degree-grid coordinates, stated as ``cell_space``."""
    def hav(a_lat, a_lng, b_lat, b_lng):
        r = 6371.0
        p1, p2 = math.radians(a_lat), math.radians(b_lat)
        dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    far = []
    for h in households:
        nearest = min((hav(h["lat"], h["lng"], f["lat"], f["lng"])
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
    return collection(feats, registry=registry)
