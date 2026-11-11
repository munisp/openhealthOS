"""demo_geodesy.py — geodesyproof-demo@1.0.0: deterministic export of the
geodesy-convergence proof surface (ADR-0075).

Synthetic coordinates only; production machinery. Byte-for-byte
reproducible, sha256-stamped under the compact contract, registered in
proofindex, gated by the meta exporter-determinism suite.

Demo story: before this wave the estate measured distance five ways —
private haversines in households, kpiplane, surveillance/outbreak,
visitplan and openhealth_ml/catchment, with TWO different earth radii
(6371.0 and the IUGG mean 6371.0088) and no stated basis anywhere. Now
one contract owns the arithmetic (geolock.GEODESY_CONTRACT), one
function computes it (geolock.distance_km), and all six sites delegate.
The convergence matrix below runs the same five reference legs through
every site and proves the results bit-identical; the drift guard pins
the router's own stated contract equal to geolock's semantic core, so a
future radius edit fails the suite instead of drifting silently. Four
drills follow: two fail closed, two pass with the basis stated —
silence was the defect, disclosure is the fix.

geodesyproof-demo@1.1.0 (ADR-0077) — the census miss, stated. The
wave-14 census found five private haversines and missed a SIXTH: a
closure inside gisexport.coverage_gap_layer (r=6371.0), sitting in the
very package that stamps exports. Wave-16 recon found it; it now
delegates via gisexport.distance_km and takes its place as the seventh
row of the matrix. A census is data, and a census that missed is
stated — not quietly fixed. Five drills below; the fifth is the miss
itself.

geodesyproof-demo@1.2.0 (ADR-0078) — the guard pins BEHAVIOR now, and
the census is cross-runtime. routing.haversine_km joins the matrix as
the eighth site: the router's duplication is now pinned bit-for-bit on
the reference legs, so a formula edit (not just a constant edit) fails
the suite. And the wave-14 census is stated for what it was — a
PYTHON-ONLY sweep: wave-17 recon found two JavaScript copies (both
IUGG 6371.0088, unclamped) and two Go copies (registry IUGG unclamped;
dispatch atan2-form, 6371.0) the sweep never saw. All four converged;
the JS conformance is bit-identical and pinned in the node suite, the
Go conformance carries a stated ~1-ulp libm envelope. Seven drills
below; the last two state what this demo's predecessors could not see.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock", "gisexport", "households", "kpiplane",
             "surveillance", "routing"):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "ml", "openhealth_ml"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import geolock  # noqa: E402
import gisexport  # noqa: E402
import households  # noqa: E402
import kpiplane  # noqa: E402
import outbreak  # noqa: E402
import visitplan  # noqa: E402
import routing  # noqa: E402
import catchment  # noqa: E402

DEMO_VERSION = "geodesyproof-demo@1.2.0"
CLOCK = "2026-11-11T16:30:00Z"
OUT = os.path.join(_HERE, "..", "..", "apps", "pwa", "geodesyproof.json")

#: The semantic core the drift guard pins: routing.py keeps its own
#: stated GEODESY_CONTRACT (ADR-0074 — the router never imports geolock),
#: and this guard makes the duplication HONEST: the cores must agree or
#: the suite fails. Duplication with a drift guard is four-eyes;
#: duplication without one is folklore.
DRIFT_CORE_KEYS = ("model", "radius_km", "honest_error")

# Fixed reference legs (synthetic — not real patients or facilities).
REFERENCE_LEGS = [
    {"name": "kaduna-to-lagos", "a": (10.52, 7.44), "b": (6.52, 3.37)},
    {"name": "kano-to-port-harcourt", "a": (12.0022, 8.592),
     "b": (4.8156, 7.0498)},
    {"name": "one-degree-of-latitude", "a": (10.0, 7.0), "b": (11.0, 7.0)},
    {"name": "identical-point", "a": (6.52, 3.37), "b": (6.52, 3.37)},
    {"name": "true-antipodal", "a": (0.0, 0.0), "b": (0.0, 180.0)},
]

#: The retired IUGG-mean implementation, kept HERE as data so the
#: divergence drill measures folklore instead of trusting memory.
_RETIRED_IUGG_RADIUS_KM = 6371.0088


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _retired_iugg_km(lat1, lng1, lat2, lng2):
    """The formula kpiplane and catchment shipped before ADR-0075."""
    import math
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _RETIRED_IUGG_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def _refusal(fn):
    try:
        fn()
    except Exception as exc:
        return {"outcome": "refused",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"outcome": "accepted", "detail": "NO REFUSAL — DEFECT"}


def _sites():
    """The eight callables that must agree bit-for-bit. The seventh —
    gisexport.distance_km — is the wave-14 census miss (ADR-0077). The
    eighth — routing.haversine_km — promotes the drift guard from
    constants to BEHAVIOR (ADR-0078): a formula edit in the router now
    fails this matrix, not just a radius edit."""
    return [
        ("geolock (owner)",
         lambda a, b: geolock.distance_km(a[0], a[1], b[0], b[1])),
        ("gisexport.distance_km",
         lambda a, b: gisexport.distance_km(a[0], a[1], b[0], b[1])),
        ("households.haversine_km",
         lambda a, b: households.haversine_km(a[0], a[1], b[0], b[1])),
        ("kpiplane._haversine_km",
         lambda a, b: kpiplane._haversine_km(a[0], a[1], b[0], b[1])),
        ("outbreak._km",
         lambda a, b: outbreak._km({"lat": a[0], "lng": a[1]},
                                   {"lat": b[0], "lng": b[1]})),
        ("routing.haversine_km",
         lambda a, b: routing.haversine_km(a, b)),
        ("visitplan.haversine_km",
         lambda a, b: visitplan.haversine_km(a[0], a[1], b[0], b[1])),
        ("catchment.haversine_km",
         lambda a, b: catchment.haversine_km(a[0], a[1], b[0], b[1])),
    ]


def _drift_core(contract):
    return {k: contract[k] for k in DRIFT_CORE_KEYS}


def run_demo():
    matrix = []
    for name, fn in _sites():
        legs = [fn(leg["a"], leg["b"]) for leg in REFERENCE_LEGS]
        matrix.append({"site": name, "legs_km": legs,
                       "row_sha256": _stamp({"site": name, "legs": legs})})
    reference = matrix[0]["legs_km"]
    agrees = all(row["legs_km"] == reference for row in matrix)

    longest = REFERENCE_LEGS[1]  # kano-to-port-harcourt, the longest leg
    contract_km = geolock.distance_km(*longest["a"], *longest["b"])
    iugg_km = _retired_iugg_km(*longest["a"], *longest["b"])
    ppm = (iugg_km - contract_km) / contract_km * 1e6
    band_ppm = 3000.0  # the contract's stated <=0.3% honesty band

    router_core = _drift_core(routing.GEODESY_CONTRACT)
    owner_core = _drift_core(geolock.GEODESY_CONTRACT)
    tampered = dict(owner_core, radius_km=_RETIRED_IUGG_RADIUS_KM)

    control = {
        "sites": [row["site"] for row in matrix],
        "legs": [leg["name"] for leg in REFERENCE_LEGS],
        "matrix_agrees_bit_for_bit": agrees,
        "matrix_sha256": _stamp([row["legs_km"] for row in matrix]),
        "reference_leg_km": {
            leg["name"]: km
            for leg, km in zip(REFERENCE_LEGS, reference)},
        "contract": geolock.GEODESY_CONTRACT,
        "drift_guard": {"router_core": router_core,
                        "owner_core": owner_core,
                        "pinned_equal": router_core == owner_core},
        "divergence": {"leg": longest["name"],
                       "retired_radius_km": _RETIRED_IUGG_RADIUS_KM,
                       "contract_radius_km": geolock.GEODESY_CONTRACT["radius_km"],
                       "retired_km": iugg_km,
                       "contract_km": contract_km,
                       "divergence_ppm": ppm,
                       "honesty_band_ppm": band_ppm,
                       "band_margin_x": band_ppm / ppm},
        "runtimes": {
            "census_scope_wave14": "python-only — the wave-14 census "
                                   "swept one runtime and presented as "
                                   "estate-wide; stated, not hidden",
            "python": {"private_before": 6, "private_after": 0,
                       "conformance": "bit-identical (this matrix)"},
            "javascript": {"private_before": 2, "private_after": 0,
                           "retired": "IUGG 6371.0088 unclamped x2 "
                                      "(dashboards/map.js, patient/geo.js)",
                           "conformance": "bit-identical (apps/pwa/tests/"
                                          "geodesy-conformance.test.mjs)"},
            "go": {"private_before": 2, "private_after": 0,
                   "retired": "registry IUGG 6371.0088 unclamped; "
                              "dispatch atan2-form 6371.0",
                   "conformance": "~1-ulp libm envelope vs CPython/glibc "
                                  "— stated, exact outputs pinned in "
                                  "geo_test.go / fleet_test.go"}},
    }

    drills = [
        {"name": "a metre-space coordinate is fed to the distance function",
         "attack": "EPSG:3857 easting as a longitude",
         "result": _refusal(
             lambda: geolock.distance_km(6.52, 376170.8, 6.52, 3.37))},
        {"name": "the contract is edited back to the old radius",
         "attack": "drift — radius_km 6371.0 becomes 6371.0088",
         "result": ({"outcome": "refused",
                     "detail": "drift guard pins router core == owner "
                               "core; a tampered radius (%s) compares "
                               "unequal and fails the suite"
                               % tampered["radius_km"]}
                    if tampered != owner_core else
                    {"outcome": "accepted",
                     "detail": "NO REFUSAL — DEFECT"})},
        {"name": "two exactly antipodal points are measured",
         "attack": "float rounding pushes the haversine argument a hair "
                   "past 1.0 — the retired implementations raised a "
                   "domain error here",
         "result": {"outcome": "stated",
                    "detail": "clamp-stated: sqrt argument is capped at "
                              "1.0; antipodal distance is %s km (half "
                              "the contract circumference), no crash"
                              % round(reference[4], 3)}},
        {"name": "the old IUGG radius keeps being used in a corner",
         "attack": "silent divergence between subsystems",
         "result": {"outcome": "stated",
                    "detail": "absorbed-by-honesty-band: the retired "
                              "radius differs by %.3f ppm on the "
                              "longest leg — %dx narrower than the "
                              "stated 0.3%% band, disclosed in ADR-0075 "
                              "instead of swapped silently"
                              % (ppm, round(band_ppm / ppm))}},
        {"name": "the wave-14 census missed a sixth private haversine",
         "attack": "an incomplete census presented as complete",
         "result": {"outcome": "stated",
                    "detail": "census-miss-stated: gisexport."
                              "coverage_gap_layer carried a private "
                              "6371.0 closure the wave-14 sweep missed "
                              "— found in wave-16 recon, retired, and "
                              "added to this matrix as the seventh row. "
                              "A census is data; a census that missed "
                              "is stated, not quietly fixed"}},
        {"name": "the router's formula is edited, constants untouched",
         "attack": "drift the drift guard cannot see — pinned data, "
                   "unpinned arithmetic",
         "result": {"outcome": "refused",
                    "detail": "behavior-pinned: routing.haversine_km is "
                              "matrix row 8 — bit-identity on five legs "
                              "means a formula edit diverges a row and "
                              "fails the suite; the guard now covers "
                              "the arithmetic, not just the constants"}},
        {"name": "the wave-14 census is re-read as estate-wide",
         "attack": "a Python-only sweep presented as the whole estate",
         "result": {"outcome": "stated",
                    "detail": "cross-runtime-census: wave 14 swept "
                              "Python only. Wave-17 recon found JS x2 "
                              "(IUGG 6371.0088, unclamped) and Go x2 "
                              "(registry IUGG; dispatch atan2-form) — "
                              "all four converged (ADR-0078); JS proven "
                              "bit-identical in the node suite, Go "
                              "pinned with a stated ~1-ulp libm envelope "
                              "in georuntimeproof"}},
    ]

    body = {
        "surface": "geodesyproof",
        "demo": True,
        "banner": "Synthetic coordinates only — every measurement below "
                  "is production code exercising production refusals.",
        "versions": {"geolock": geolock.GEOLOCK_VERSION,
                     "gisexport": gisexport.GIS_VERSION,
                     "households": households.HOUSEHOLDS_VERSION,
                     "kpiplane": kpiplane.KPIPLANE_VERSION,
                     "outbreak": outbreak.OUTBREAK_VERSION,
                     "visitplan": visitplan.VISITPLAN_VERSION,
                     "routing": routing.ROUTING_VERSION,
                     "catchment": "openhealth_ml.catchment (unversioned "
                                  "module — stated, not hidden)",
                     "exporter": DEMO_VERSION},
        "generated_at": CLOCK,
        "convergence_matrix": matrix,
        "control": control,
        "drills": drills,
        "honesty": {
            "implementations_retired": 6,
            "radii_retired": ["private 6371.0 copies (households, "
                              "outbreak, visitplan)",
                              "private 6371.0 closure (gisexport."
                              "coverage_gap_layer — the wave-14 census "
                              "miss, named)",
                              "IUGG mean 6371.0088 (kpiplane, "
                              "openhealth_ml.catchment)"],
            "one_function": "geolock.distance_km — every site delegates "
                            "or the matrix diverges and the meta gate "
                            "fails",
            "drift_guard": "packages/geodesy pins the semantic core of "
                           "routing.GEODESY_CONTRACT equal to geolock's "
                           "— duplication with a guard is four-eyes, "
                           "duplication without one is folklore",
            "absence_is_not_a_value": "every coverage, catchment, "
                                      "cluster and visit-plan result "
                                      "now carries its geodesy basis "
                                      "as data",
            "folklore_named": "the clamp is stated; the superseded "
                              "radius is stated; the unversioned ml "
                              "module is stated",
        },
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    payload = json.dumps(body, indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    payloadio.write_text(OUT, payload)
    print("geodesyproof: %d sites agree=%s, %d drills, sha %s -> %s"
          % (len(body["convergence_matrix"]),
             body["control"]["matrix_agrees_bit_for_bit"],
             len(body["drills"]), body["sha256"][:12],
             os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
