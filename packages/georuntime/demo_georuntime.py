"""demo_georuntime.py — georuntimeproof-demo@1.0.0: deterministic export
of the cross-runtime geodesy proof surface (ADR-0078).

Synthetic coordinates only; production machinery. Byte-for-byte
reproducible, sha256-stamped under the compact contract, registered in
proofindex, gated by the meta exporter-determinism suite.

Demo story: the wave-14 geodesy census swept ONE runtime and presented
as estate-wide. Wave-17 recon asked the doctrine question — where else
does the estate measure distance? — and found the estate measured it
four ways across three runtimes: Python (converged in waves 14/16),
JavaScript (TWO private copies of the retired IUGG radius, unclamped:
apps/dashboards/js/map.js and apps/patient/js/geo.js), and Go
(registry-service IUGG unclamped; dispatch-service atan2-form — a local
preference, not the contract). All four converged on
geolock.GEODESY_CONTRACT: one radius, one asin-form formula, one stated
clamp. The five reference legs below are the CONFORMANCE VECTORS: the
node suite asserts the JS module bit-identical to these Python-computed
bytes (CPython/glibc and V8 agree bit-for-bit); the Go suites pin their
exact outputs as literals and carry a stated ~1-ulp libm envelope against
these vectors. A claim per runtime, a suite per claim, an envelope where
the silicon disagrees — stated, never hidden.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock", "geodesy"):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))

import geolock  # noqa: E402

DEMO_VERSION = "georuntimeproof-demo@1.0.0"
CLOCK = "2026-11-11T18:00:00Z"
OUT = os.path.join(_HERE, "..", "..", "apps", "pwa",
                   "georuntimeproof.json")

#: The five conformance vectors — the same reference legs the
#: geodesyproof matrix runs. These bytes are what the node suite
#: asserts bit-identity against and what the Go suites pin with a
#: stated ~1-ulp envelope.
REFERENCE_LEGS = [
    {"name": "kaduna-to-lagos", "a": (10.52, 7.44), "b": (6.52, 3.37)},
    {"name": "kano-to-port-harcourt", "a": (12.0022, 8.592),
     "b": (4.8156, 7.0498)},
    {"name": "one-degree-of-latitude", "a": (10.0, 7.0), "b": (11.0, 7.0)},
    {"name": "identical-point", "a": (6.52, 3.37), "b": (6.52, 3.37)},
    {"name": "true-antipodal", "a": (0.0, 0.0), "b": (0.0, 180.0)},
]

#: Go's exact outputs for the vectors, measured on Go 1.22.12
#: (pure-Go fdlibm-derived libm) — the conformance epsilon exists
#: because of rows 1-2; rows 3-5 are bit-identical to Python.
GO_EXACT = {
    "kaduna-to-lagos": 630.91857880951272,
    "kano-to-port-harcourt": 816.89671017737521,
    "one-degree-of-latitude": 111.19492664455873,
    "identical-point": 0.0,
    "true-antipodal": 20015.086796020572,
}


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _retired_atan2_km(lat1, lng1, lat2, lng2):
    """dispatch-service's retired atan2-form variant, kept HERE as data
    so its divergence from the contract is measured, not remembered."""
    rad = math.pi / 180
    dp = (lat2 - lat1) * rad
    dl = (lng2 - lng1) * rad
    a = (math.sin(dp / 2) ** 2
         + math.cos(lat1 * rad) * math.cos(lat2 * rad)
         * math.sin(dl / 2) ** 2)
    return 2 * 6371.0 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def run_demo():
    vectors = []
    for leg in REFERENCE_LEGS:
        a, b = leg["a"], leg["b"]
        py_km = geolock.distance_km(a[0], a[1], b[0], b[1])
        go_km = GO_EXACT[leg["name"]]
        atan2_km = _retired_atan2_km(a[0], a[1], b[0], b[1])
        vectors.append({
            "name": leg["name"], "a": list(a), "b": list(b),
            "km": py_km,
            "go_exact_km": go_km,
            "go_vs_python_km": abs(go_km - py_km),
            "retired_atan2_km": atan2_km,
            "atan2_vs_contract_km": abs(atan2_km - py_km),
        })

    worst_go = max(v["go_vs_python_km"] for v in vectors)
    worst_atan2 = max(v["atan2_vs_contract_km"] for v in vectors)

    control = {
        "contract": geolock.GEODESY_CONTRACT,
        "vectors_sha256": _stamp([(v["name"], v["km"]) for v in vectors]),
        "runtimes": {
            "python": {
                "owner": "geolock.distance_km",
                "private_before": 6, "private_after": 0,
                "conformance": "bit-identical — the geodesyproof matrix "
                               "runs eight Python callables on these "
                               "legs and every row digest verifies"},
            "javascript": {
                "owner": "apps/pwa/js/geodesy.js distanceKm",
                "private_before": 2, "private_after": 0,
                "retired": ["apps/dashboards/js/map.js — IUGG 6371.0088, "
                            "unclamped",
                            "apps/patient/js/geo.js — IUGG 6371.0088, "
                            "unclamped"],
                "conformance": "bit-identical — apps/pwa/tests/"
                               "geodesy-conformance.test.mjs asserts === "
                               "against these vectors"},
            "go": {
                "owner": "registry-service geo.go / dispatch-service "
                         "fleet.go — contract formula, stated clamp",
                "private_before": 2, "private_after": 0,
                "retired": ["registry-service — IUGG 6371.0088, "
                            "unclamped",
                            "dispatch-service — atan2-form 6371.0 (a "
                            "local preference, not the contract)"],
                "conformance": "exact outputs pinned as literals in "
                               "geo_test.go / fleet_test.go; vs these "
                               "Python vectors the envelope is stated",
                "libm_envelope": {
                    "worst_divergence_km": worst_go,
                    "envelope_km": 1e-9,
                    "meaning": "Go's fdlibm-derived libm differs from "
                               "CPython/glibc by ~1 ulp on some "
                               "legs — at 630 km that is about a tenth "
                               "of a micrometre; planning-irrelevant, "
                               "stated because the checksum is the "
                               "contract"},
            },
        },
        "census_scope_stated": "wave 14 swept Python only; this census "
                               "swept packages/ ml/ apps/ services/ "
                               "across .py .js .mjs .go .rs — method: "
                               "grep for asin|Asin|6371|atan2|Atan2",
    }

    drills = [
        {"name": "a metre-space coordinate is fed to the JS distance",
         "attack": "EPSG:3857 easting as a longitude in the PWA",
         "result": {"outcome": "refused",
                    "proof": "apps/pwa/tests/geodesy-conformance.test.mjs",
                    "detail": "distanceKm throws RangeError naming "
                              "lng1 376170.8 — the same offender the "
                              "Python gate names; the web tier fails "
                              "closed in the same words"}},
        {"name": "two exactly antipodal points are measured in the PWA",
         "attack": "float rounding past 1.0 — the retired JS copies "
                   "returned NaN here",
         "result": {"outcome": "stated",
                    "proof": "apps/pwa/tests/geodesy-conformance.test.mjs",
                    "detail": "clamp-stated: distanceKm(0,0, 0,180) === "
                              "%s — bit-identical to the Python vector, "
                              "no NaN" % repr(vectors[4]["km"])}},
        {"name": "a reference vector is edited by one digit",
         "attack": "silent contract drift in the conformance surface",
         "result": {"outcome": "refused",
                    "proof": "apps/pwa/tests/geodesy-conformance.test.mjs",
                    "detail": "the node suite asserts === against this "
                              "payload's bytes — a tampered vector fails "
                              "the suite and the tampered payload fails "
                              "proofindex; two independent gates"}},
        {"name": "Go's outputs are presented as bit-identical to "
                 "Python's",
         "attack": "an overclaim of cross-runtime identity",
         "result": {"outcome": "stated",
                    "detail": "envelope-stated: Go's libm diverges by "
                              "up to %.1e km (~1 ulp) on these vectors — "
                              "the payload carries go_exact_km per leg "
                              "and the Go tests pin THOSE bytes; nobody "
                              "claims the silicon agrees"
                              % worst_go}},
        {"name": "the dispatch service keeps its atan2 form",
         "attack": "a better-loved local formula over the shared "
                   "contract",
         "result": {"outcome": "stated",
                    "detail": "retired-measured: the atan2 form "
                              "diverges from the contract by up to %.1e "
                              "km on these vectors (sub-nanometre, "
                              "absorbed); one formula estate-wide — "
                              "convergence beats local preference"
                              % worst_atan2}},
        {"name": "the wave-14 census is quoted as the whole estate",
         "attack": "a single-runtime sweep re-presented as complete",
         "result": {"outcome": "stated",
                    "detail": "scope-stated: wave 14 swept Python; "
                              "waves 16/17 closed what it missed "
                              "(gisexport's closure, then the JS and Go "
                              "copies). The census now names its method "
                              "and its runtimes — a census with a stated "
                              "scope can be audited; one without cannot "
                              "be trusted"}},
    ]

    body = {
        "surface": "georuntimeproof",
        "demo": True,
        "banner": "Synthetic coordinates only — every distance below is "
                  "production code exercising production refusals.",
        "versions": {"geolock": geolock.GEOLOCK_VERSION,
                     "exporter": DEMO_VERSION,
                     "go_toolchain": "go1.22.12 linux/amd64 (conformance "
                                     "literals measured there — stated)"},
        "generated_at": CLOCK,
        "conformance_vectors": vectors,
        "control": control,
        "drills": drills,
        "honesty": {
            "one_contract": "every runtime computes spherical haversine "
                            "at 6371.0 with the stated clamp — or names "
                            "why not",
            "bit_identity_where_true": "Python and JavaScript agree "
                "bit-for-bit on these vectors and the node suite pins "
                "it; Go's ~1-ulp libm envelope is stated as data instead "
                "of claimed away",
            "the_payload_is_the_vectors": "the node suite reads THIS "
                "payload's bytes — tamper with a vector and both "
                "proofindex and the conformance suite go red",
            "census_scope": "every census now states its runtimes and "
                            "its method; an unscoped census is a claim, "
                            "not a measurement",
        },
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    payload = json.dumps(body, indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(payload)
    print("georuntimeproof: %d vectors, %d drills, worst go divergence "
          "%.2e km, sha %s -> %s"
          % (len(body["conformance_vectors"]), len(body["drills"]),
             max(v["go_vs_python_km"] for v in body["conformance_vectors"]),
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
