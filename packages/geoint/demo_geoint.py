"""demo_geoint.py — geointproof-demo@1.0.0: deterministic export of the
geospatial pipeline's governed proof surface (ADR-0074).

Synthetic fixtures only; production machinery. Byte-for-byte
reproducible, sha256-stamped under the compact contract, registered in
proofindex, gated by the meta exporter-determinism suite.

Demo story: one governed pipeline end to end — geolock preflight (the
pinned known-answer vectors) → a facilities export whose CRS contract is
registry-governed and digest-pinned → a household density layer whose
centroids are averaged in UTM metres, not degrees → a road graph built
from an extract whose geolock member declares the CRS, upgraded to
registry-governed basis, source pinned by digest → an A* route carrying
its own digest. Six attacks follow: four fail closed, two pass with the
basis stated — silence was the defect, disclosure is the fix.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock", "gisexport", "routing"):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import geolock  # noqa: E402
import gisexport  # noqa: E402
import routing  # noqa: E402

DEMO_VERSION = "geointproof-demo@1.0.0"
CLOCK = "2026-11-11T16:00:00Z"
OUT = os.path.join(_HERE, "..", "..", "apps", "pwa", "geointproof.json")

# Synthetic Lagos fixtures — not real facilities or households.
FACILITIES = [
    {"id": "PHC-DEMO-1", "name": "Synthetic Ikeja PHC", "kind": "phc",
     "lat": 6.601, "lng": 3.351, "beds": 12,
     "services": ["immunization", "anc"]},
    {"id": "PHC-DEMO-2", "name": "Synthetic Yaba PHC", "kind": "phc",
     "lat": 6.515, "lng": 3.379, "beds": 20,
     "services": ["immunization", "emergency"]},
    {"id": "GH-DEMO-1", "name": "Synthetic Lagos GH", "kind": "general",
     "lat": 6.454, "lng": 3.402, "beds": 120,
     "services": ["emergency", "surgery"]},
]

HOUSEHOLDS = [{"lat": 6.520 + 0.001 * i, "lng": 3.370 + 0.001 * i,
               "members": 4} for i in range(8)]

ROADS_FEATURES = [
    {"type": "Feature", "properties": {"highway": "residential"},
     "geometry": {"type": "LineString",
                  "coordinates": [[3.370, 6.520], [3.379, 6.520],
                                  [3.379, 6.529]]}},
    {"type": "Feature", "properties": {"highway": "motorway"},
     "geometry": {"type": "LineString",
                  "coordinates": [[3.370, 6.520], [3.370, 6.529],
                                  [3.379, 6.529]]}},
]


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _refusal(fn):
    try:
        fn()
    except (geolock.GeolockError, gisexport.Error, routing.Error) as exc:
        return {"outcome": "refused", "reason": str(exc)}
    return {"outcome": "NOT REFUSED — DEFECT"}


def _seed_registry():
    reg = geolock.CRSRegistry()
    for code, zone, hemi, name in (
            ("EPSG:32631", 31, "N", "WGS 84 / UTM zone 31N"),
            ("EPSG:32632", 32, "N", "WGS 84 / UTM zone 32N"),
            ("EPSG:32627", 27, "N", "WGS 84 / UTM zone 27N"),
            ("EPSG:32734", 34, "S", "WGS 84 / UTM zone 34S")):
        reg.register(code, kind="projected", projection="utm",
                     name=name, params={"zone": zone, "hemisphere": hemi})
    return reg


def _governed_roads():
    """A roads extract carrying the geolock foreign member — the shape a
    governed pipeline hands the router."""
    feats = copy.deepcopy(ROADS_FEATURES)
    fc = {"type": "FeatureCollection", "features": feats}
    fc["openhealth:geolock"] = {
        "crs": "EPSG:4326", "axis_order": "lng,lat", "governed": True,
        "features_sha256": _stamp(feats)}
    return fc


def run_demo():
    reg = _seed_registry()
    kat = geolock.verify_known_answers(reg)

    facilities = gisexport.facilities_layer(FACILITIES, registry=reg)
    density = gisexport.households_density_layer(HOUSEHOLDS,
                                                 registry=reg)
    privacy = gisexport.households_density_layer(
        HOUSEHOLDS[:6] + [{"lat": 7.50, "lng": 4.50, "members": 3}],
        registry=reg)

    graph = routing.build_graph_from_geojson(_governed_roads(),
                                             registry=reg)
    net = routing.RoadGraph(graph)
    route = net.route(6.520, 3.370, 6.529, 3.379)

    control = {
        "preflight": {"known_answers": kat["status"],
                      "vectors": len(kat["vectors"]),
                      "tolerance_m": geolock.KAT_TOLERANCE},
        "facilities_export": {
            "features": len(facilities["features"]),
            "governed": facilities["openhealth:geolock"]["governed"],
            "basis": facilities["openhealth:geolock"]["basis"],
            "features_sha256":
                facilities["openhealth:geolock"]["features_sha256"]},
        "density_layer": {
            "features": len(density["features"]),
            "centroid_space":
                density["features"][0]["properties"]["centroid_space"],
            "centroid_crs":
                density["features"][0]["properties"]["centroid_crs"]},
        "privacy_floor": {
            "households_in": 7,
            "features_out": len(privacy["features"]),
            "suppressed_sparse_cell": len(privacy["features"]) == 1},
        "road_graph": {
            "nodes": len(graph["nodes"]),
            "basis": graph["crs_contract"]["basis"],
            "geodesy": graph["crs_contract"]["geodesy"]["model"],
            "source_sha256": graph["source_sha256"]},
        "route": {"distance_km": route["distance_km"],
                  "eta_min": route["eta_min"],
                  "route_sha256": route["route_sha256"]},
    }

    metre_extract = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"highway": "primary"},
         "geometry": {"type": "LineString",
                      "coordinates": [[376170.8, 727867.6],
                                      [376200.0, 727900.0]]}}]}
    legacy_3857 = {"type": "FeatureCollection",
                   "crs": {"type": "name",
                           "properties": {"name": "EPSG:3857"}},
                   "features": copy.deepcopy(ROADS_FEATURES)}
    legacy_density = gisexport.households_density_layer(HOUSEHOLDS)
    undeclared = routing.build_graph_from_geojson(
        {"type": "FeatureCollection",
         "features": copy.deepcopy(ROADS_FEATURES)})

    drills = [
        {"name": "a metre-space extract is fed to the router",
         "attack": "EPSG:3857 coordinates as degrees",
         "result": _refusal(
             lambda: routing.build_graph_from_geojson(metre_extract))},
        {"name": "a legacy extract declares the wrong CRS",
         "attack": "pre-7946 crs member naming EPSG:3857",
         "result": _refusal(
             lambda: routing.build_graph_from_geojson(legacy_3857))},
        {"name": "an export demands a CRS nobody registered",
         "attack": "governed export against an empty registry",
         "result": _refusal(
             lambda: gisexport.facilities_layer(
                 FACILITIES,
                 registry=geolock.CRSRegistry(seeded=False)))},
        {"name": "a graph build demands a CRS nobody registered",
         "attack": "registry-governed build, empty registry",
         "result": _refusal(
             lambda: routing.build_graph_from_geojson(
                 _governed_roads(),
                 registry=geolock.CRSRegistry(seeded=False)))},
        {"name": "a centroid is averaged in degrees",
         "attack": "density layer with no registry injected",
         "result": {"outcome": "stated",
                    "detail": "centroid_space is %s — the degree-"
                              "average is disclosed on the feature, "
                              "not hidden" %
                              legacy_density["features"][0]
                              ["properties"]["centroid_space"]}},
        {"name": "an extract arrives with no CRS declaration at all",
         "attack": "undeclared roads extract",
         "result": {"outcome": "stated",
                    "detail": "graph basis is %s — the RFC 7946 "
                              "default is disclosed, not assumed "
                              "silently"
                              % undeclared["crs_contract"]["basis"]}},
    ]

    body = {
        "surface": "geointproof",
        "demo": True,
        "banner": "Synthetic geography only — every layer below is "
                  "production code exercising production refusals.",
        "versions": {"geolock": geolock.GEOLOCK_VERSION,
                     "gisexport": gisexport.GIS_VERSION,
                     "routing": routing.ROUTING_VERSION,
                     "exporter": DEMO_VERSION},
        "generated_at": CLOCK,
        "pipeline": ["geolock preflight", "governed facilities export",
                     "metre-space density centroids",
                     "CRS-gated graph build", "digested A* route"],
        "control": control,
        "drills": drills,
        "honesty": {
            "folklore_named": "degree-space centroids and assumed CRS "
                              "bases are disclosed on the artifact — "
                              "silence was the defect, disclosure is "
                              "the fix",
            "crs_as_data": "the CRS is a registry entry; unregistered "
                           "is a named refusal at export AND at graph "
                           "build",
            "injection_not_import": "routing never imports geolock — "
                                    "the registry is injected like a "
                                    "clock; the seam is the protocol",
            "engine_never_embedded": "reprojection to the heavy "
                                     "formats composes through "
                                     "geolock's ratified adapters; "
                                     "nothing is embedded (ADR-0005)",
            "privacy_floor_holds": "k-anonymity suppression binds under "
                                   "governed export — sparse cells "
                                   "never leak",
        },
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    payload = json.dumps(body, indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    payloadio.write_text(OUT, payload)
    print("geointproof: %d drills, route %s km, sha %s -> %s"
          % (len(body["drills"]), body["control"]["route"]["distance_km"],
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
