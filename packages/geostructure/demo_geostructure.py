"""demo_geostructure.py — geostructureproof-demo@1.0.0: deterministic
export of the structure-gate proof surface (ADR-0077).

Synthetic fixtures only; production machinery. Byte-for-byte
reproducible, sha256-stamped under the compact contract, registered in
proofindex, gated by the meta exporter-determinism suite.

Demo story: wave 15 taught the export gate to range-check every
coordinate before stamping — but range is not SHAPE. An in-range yet
malformed geometry (an unclosed ring, a Point wearing a MultiPoint's
coordinates, a bbox that contradicts its own feature) used to be
stamped faithfully: the checksum is the contract, and the contract was
pinning bytes that had never been checked for structure. gisexport@1.3.0
gates RFC 7946 structure BEFORE range BEFORE stamp, with the offender
and feature index named, and discloses what it checked as data. The
same wave closes the wave-14 census miss: gisexport.coverage_gap_layer
carried a SIXTH private haversine the convergence sweep missed — it now
delegates to geolock.distance_km, and its collections carry the geodesy
contract. A census is data; a census that missed is stated, not quietly
fixed. Seven drills fail closed; three pass with the basis stated.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock", "gisexport"):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import geolock  # noqa: E402
import gisexport  # noqa: E402

DEMO_VERSION = "geostructureproof-demo@1.0.0"
CLOCK = "2026-11-11T17:30:00Z"
OUT = os.path.join(_HERE, "..", "..", "apps", "pwa",
                   "geostructureproof.json")

# Synthetic Kaduna-ward fixtures — not real catchments, roads or people.
CATCHMENT_RING = [[7.40, 10.50], [7.46, 10.50], [7.46, 10.56],
                  [7.40, 10.56], [7.40, 10.50]]
INNER_RING = [[7.42, 10.52], [7.44, 10.52], [7.44, 10.54],
              [7.42, 10.54], [7.42, 10.52]]

WELL_FORMED = [
    {"type": "Feature", "bbox": [7.40, 10.50, 7.46, 10.56],
     "geometry": {"type": "MultiPolygon",
                  "coordinates": [[CATCHMENT_RING, INNER_RING]]},
     "properties": {"id": "CATCHMENT-DEMO-1", "kind": "synthetic"}},
    {"type": "Feature",
     "geometry": {"type": "LineString",
                  "coordinates": [[7.40, 10.50], [7.43, 10.53],
                                  [7.46, 10.56]]},
     "properties": {"id": "ROAD-DEMO-1", "highway": "synthetic"}},
    {"type": "Feature",
     "geometry": {"type": "Point", "coordinates": [7.43, 10.53, 612.0]},
     "properties": {"id": "MAST-DEMO-1", "note": "elevation stated"}},
]

GAP_FACILITIES = [{"lat": 10.50, "lng": 7.40}]
GAP_HOUSEHOLDS = [{"lat": 10.90 + 0.001 * i, "lng": 7.40,
                   "members": 4} for i in range(6)]


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _refusal(fn):
    try:
        fn()
    except gisexport.Error as exc:
        return {"outcome": "refused", "reason": str(exc)}
    return {"outcome": "NOT REFUSED — DEFECT"}


def _feature(geometry, **extra):
    feat = {"type": "Feature", "geometry": geometry, "properties": {}}
    feat.update(extra)
    return feat


def run_demo():
    registry = geolock.CRSRegistry()
    governed = gisexport.collection(WELL_FORMED, registry=registry)
    member = governed["openhealth:geolock"]

    gap_fc = gisexport.coverage_gap_layer(
        GAP_FACILITIES, GAP_HOUSEHOLDS, max_km=5.0, registry=registry)
    gap_geodesy = gap_fc["openhealth:geodesy"]

    null_fc = gisexport.collection(
        [_feature(None, note="ungeolocated fixture")], registry=registry)
    elev_fc = gisexport.collection([WELL_FORMED[2]], registry=registry)

    control = {
        "governed_export": {
            "features": len(governed["features"]),
            "governed": member["governed"],
            "basis": member["basis"],
            "structure_checked": member["structure_checked"],
            "coordinates_checked": member["coordinates_checked"],
            "features_sha256": member["features_sha256"],
        },
        "coverage_gap_geodesy": {
            "basis": gap_geodesy["basis"],
            "radius_km": gap_geodesy["contract"]["radius_km"],
            "supersedes": gap_geodesy["supersedes"],
            "unserved_cells": len(gap_fc["features"]),
        },
        "census": {
            "private_haversines_before_wave14": 6,
            "private_haversines_after_wave16": 0,
            "method": "grep -rn 'asin\\|6371' --include=*.py packages/ "
                      "ml/ | excluding geolock (owner), demos, tests, and "
                      "named-retirement data",
            "intentional_copies_stated": [
                "routing.py keeps its own guarded haversine — the router "
                "never imports geolock (ADR-0074); packages/geodesy's "
                "drift guard pins the semantic cores equal or the suite "
                "fails. Duplication with a guard is four-eyes.",
            ],
            "wave14_miss_stated": "gisexport.coverage_gap_layer's "
                                  "closure was missed by the wave-14 "
                                  "census and retired in wave 16 — "
                                  "named, not quietly fixed",
        },
    }

    def attempt(feats):
        return lambda: gisexport.collection(feats, registry=registry)

    drills = [
        {"name": "a polygon ring is left unclosed",
         "attack": "first position != last position (RFC 7946 §3.1.6)",
         "result": _refusal(attempt([_feature(
             {"type": "Polygon",
              "coordinates": [[[7.40, 10.50], [7.46, 10.50],
                               [7.46, 10.56], [7.40, 10.56]]]})]))},
        {"name": "a ring arrives with only three positions",
         "attack": "§3.1.6 requires at least four",
         "result": _refusal(attempt([_feature(
             {"type": "Polygon",
              "coordinates": [[[7.40, 10.50], [7.46, 10.50],
                               [7.40, 10.50]]]})]))},
        {"name": "a LineString carries a single position",
         "attack": "a line needs two (§3.1.4)",
         "result": _refusal(attempt([_feature(
             {"type": "LineString",
              "coordinates": [[7.43, 10.53]]})]))},
        {"name": "a Point wears a MultiPoint's coordinates",
         "attack": "shape smuggled past the wave-15 range gate",
         "result": _refusal(attempt([_feature(
             {"type": "Point",
              "coordinates": [[7.43, 10.53], [7.44, 10.54]]})]))},
        {"name": "a geometry arrives with empty coordinates",
         "attack": "emptiness instead of an honest null (§3.2)",
         "result": _refusal(attempt([_feature(
             {"type": "LineString", "coordinates": []})]))},
        {"name": "a bbox contradicts its own feature",
         "attack": "bbox [7.40,10.50,7.41,10.51] around a point at "
                   "7.43,10.53",
         "result": _refusal(attempt([
             _feature({"type": "Point", "coordinates": [7.43, 10.53]},
                      bbox=[7.40, 10.50, 7.41, 10.51])]))},
        {"name": "a position carries a fourth element",
         "attack": "beyond elevation the estate has no contract",
         "result": _refusal(attempt([_feature(
             {"type": "LineString",
              "coordinates": [[7.40, 10.50], [7.46, 10.56, 612.0, 1.0]]})]))},
        {"name": "a feature is genuinely ungeolocated",
         "attack": "null geometry",
         "result": {"outcome": "stated",
                    "detail": "legal per RFC 7946 §3.2 — counted as "
                              "null_geometries=%d, zero positions "
                              "checked, absence stated rather than "
                              "fabricated"
                              % null_fc["openhealth:geolock"]
                                        ["structure_checked"]
                                        ["null_geometries"]}},
        {"name": "a point carries elevation",
         "attack": "third position element",
         "result": {"outcome": "stated",
                    "detail": "allowed per §3.1.1 and disclosed — "
                              "elevations=%d on the member; beyond "
                              "elevation the gate refuses (drill 7)"
                              % elev_fc["openhealth:geolock"]
                                       ["structure_checked"]
                                       ["elevations"]}},
        {"name": "the wave-14 census is re-presented as complete",
         "attack": "an incomplete census presented as complete",
         "result": {"outcome": "stated",
                    "detail": "the miss is ON this payload — census."
                              "wave14_miss_stated names the closure the "
                              "sweep overlooked; the retired count went "
                              "5 to 6 in daylight"}},
    ]

    body = {
        "surface": "geostructureproof",
        "demo": True,
        "banner": "Synthetic fixtures only — every geometry below is "
                  "production code exercising production refusals.",
        "versions": {"gisexport": gisexport.GIS_VERSION,
                     "geolock": geolock.GEOLOCK_VERSION,
                     "exporter": DEMO_VERSION},
        "generated_at": CLOCK,
        "control": control,
        "drills": drills,
        "honesty": {
            "shape_before_range_before_stamp": "structure is gated "
                "before coordinates are ranged before bytes are pinned "
                "— an in-range malformed geometry is still malformed",
            "census_is_data": "the wave-14 census missed a sixth "
                "private haversine in the gate package itself; the "
                "miss is stated on the artifact, not quietly fixed",
            "guarded_duplication": "routing.py's own haversine is "
                "intentional and drift-guarded; gisexport's closure "
                "was neither — intention is the difference",
            "absence_is_not_a_value": "empty coordinates are refused; "
                "a null geometry is how the RFC says absent",
        },
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    payload = json.dumps(body, indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    payloadio.write_text(OUT, payload)
    sc = body["control"]["governed_export"]["structure_checked"]
    print("geostructureproof: %d geometries / %d rings checked, %d "
          "drills, sha %s -> %s"
          % (sc["geometries"], sc["rings"], len(body["drills"]),
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
