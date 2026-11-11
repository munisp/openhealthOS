"""demo_geolock.py — geoproof-demo@1.0.0: deterministic export of the
coordinate-governance seam's proof surface.

Synthetic fixtures only; production machinery. Byte-for-byte
reproducible, sha256-stamped under the compact contract, registered in
proofindex, gated by the meta exporter-determinism suite.

Demo story: the estate's two seeded CRSs plus three deployment-registered
UTM zones (Lagos 31N, Abuja 32N, Praia 27N); all six pinned known-answer
vectors verified against the live formulas; a Lagos catchment polygon
measured in km²; a federated heavy-I/O call through the ratified
terrio-cli adapter over an injected executor; and eight attacks, all
named.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import geolock  # noqa: E402

DEMO_VERSION = "geoproof-demo@1.0.0"
CLOCK = "2026-11-11T14:00:00Z"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "apps", "pwa", "geoproof.json")

# A synthetic 2 km x 2 km catchment square around Ikeja, Lagos, in
# UTM 31N metres (fixture coordinates, not a real facility).
_CATCHMENT = [(540924.0, 720189.0), (542924.0, 720189.0),
              (542924.0, 722189.0), (540924.0, 722189.0)]


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _refusal(fn):
    try:
        fn()
    except geolock.GeolockError as exc:
        return {"outcome": "refused", "reason": str(exc)}
    return {"outcome": "NOT REFUSED — DEFECT"}


def _seed_registry():
    reg = geolock.CRSRegistry()
    reg.register("EPSG:32631", kind="projected", projection="utm",
                 name="WGS 84 / UTM zone 31N",
                 params={"zone": 31, "hemisphere": "N"})
    reg.register("EPSG:32632", kind="projected", projection="utm",
                 name="WGS 84 / UTM zone 32N",
                 params={"zone": 32, "hemisphere": "N"})
    reg.register("EPSG:32627", kind="projected", projection="utm",
                 name="WGS 84 / UTM zone 27N",
                 params={"zone": 27, "hemisphere": "N"})
    reg.register("EPSG:32734", kind="projected", projection="utm",
                 name="WGS 84 / UTM zone 34S",
                 params={"zone": 34, "hemisphere": "S"})
    return reg


def _seed_adapters():
    adapters = geolock.AdapterRegistry()
    adapters.ratify("terrio-cli", by="ada.lovelace")
    adapters.ratify("terrio-cli", by="bola.tinubu")
    return adapters


def _executor(command):
    """Injected deterministic executor: the wire is a pure function
    of the command — no subprocess in a proof surface."""
    result = {"command": command, "answer": "synthetic-cog-window"}
    result["sha256"] = _stamp(result)
    return result


def _broken_executor(command):
    raise RuntimeError("simulated engine failure — stated, never "
                       "swallowed")


def run_demo():
    reg = _seed_registry()
    adapters = _seed_adapters()

    kat = geolock.verify_known_answers(reg)
    rt = geolock.verify_roundtrip(reg, "EPSG:32631",
                                  541924.3013987611, 721189.2172458889)
    lagos = geolock.point(reg, "EPSG:4326", 3.3792, 6.5244,
                          subject="synthetic-facility")
    projected = geolock.transform(reg, "EPSG:4326", "EPSG:32631",
                                  3.3792, 6.5244)
    area = geolock.area_km2(reg, "EPSG:32631", _CATCHMENT)

    control = {
        "known_answers": kat,
        "roundtrip": rt,
        "facility_point": lagos,
        "facility_projected": projected,
        "catchment_area_km2": area,
        "zone_inference": {"lagos_lng_3.3792":
                           geolock.utm_zone_for(3.3792)},
        "adapter_call": adapters.run(
            "terrio-cli",
            ["info", "s3://synthetic/cog.tif", "--json"],
            executor=_executor, executed_at=CLOCK),
    }

    drills = [
        {"name": "a coordinate arrives in a CRS nobody registered",
         "attack": "unregistered CRS transform",
         "result": _refusal(
             lambda: geolock.transform(reg, "EPSG:4326",
                                       "EPSG:99999",
                                       3.3792, 6.5244))},
        {"name": "an area is demanded in degrees",
         "attack": "area in a geographic CRS",
         "result": _refusal(
             lambda: geolock.area_km2(reg, "EPSG:4326",
                                      [(3.3, 6.5), (3.4, 6.5),
                                       (3.4, 6.6)]))},
        {"name": "a point claims the far side of the world",
         "attack": "out-of-range coordinate",
         "result": _refusal(
             lambda: geolock.point(reg, "EPSG:4326", 3.3792,
                                   96.5244))},
        {"name": "a UTM CRS is registered without its zone",
         "attack": "UTM with missing params",
         "result": _refusal(
             lambda: reg.register("EPSG:32633", kind="projected",
                                  projection="utm",
                                  name="zoneless"))},
        {"name": "a projection outside the closed vocabulary",
         "attack": "unknown projection",
         "result": _refusal(
             lambda: reg.register("LOCAL:1", kind="projected",
                                  projection="oblique-stereographic",
                                  name="local grid"))},
        {"name": "a template engine runs before ratification",
         "attack": "gdal-cli, zero approvers",
         "result": adapters.run("gdal-cli", ["gdalinfo", "x.tif"],
                                executor=_executor,
                                executed_at=CLOCK)},
        {"name": "a ratified engine has no wire",
         "attack": "missing executor",
         "result": adapters.run("terrio-cli", ["info", "x.tif"],
                                executed_at=CLOCK)},
        {"name": "the engine fails mid-command",
         "attack": "executor error",
         "result": adapters.run("terrio-cli", ["info", "x.tif"],
                                executor=_broken_executor,
                                executed_at=CLOCK)},
    ]

    body = {
        "surface": "geoproof",
        "demo": True,
        "banner": "Synthetic coordinates only — every engine below "
                  "is production code exercising production refusals.",
        "versions": {"geolock": geolock.GEOLOCK_VERSION,
                     "exporter": DEMO_VERSION},
        "generated_at": CLOCK,
        "registry": {
            "systems": len(reg.systems),
            "seeded": 2,
            "deployment_registered": len(reg.systems) - 2,
            "kat_vectors": len(geolock.KNOWN_ANSWERS),
            "kat_tolerance_m": geolock.KAT_TOLERANCE,
            "adapters": len(adapters.adapters),
        },
        "control": control,
        "drills": drills,
        "honesty": {
            "crs_as_data": "every CRS is a registry entry — WGS-84 "
                           "and Web Mercator seeded, UTM zones "
                           "deployment-registered; the zone follows "
                           "the coordinate, not the codebase",
            "known_answers_are_data": "every projection is pinned by "
                                      "control vectors verified "
                                      "against PROJ at pin time, "
                                      "tolerance stated",
            "area_needs_projection": "area in degrees is refused — "
                                     "degrees are not distances",
            "engine_never_embedded": "terrio/GDAL compose via "
                                     "ratified adapters + injected "
                                     "executor; nothing is embedded "
                                     "(ADR-0005)",
            "absence_is_not_a_value": "a failed engine call is "
                                      "stated NOT RUN — never an "
                                      "empty result",
        },
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    payload = json.dumps(body, indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    payloadio.write_text(OUT, payload)
    print("geoproof: %d CRSs, %d drills, sha %s -> %s"
          % (body["registry"]["systems"], len(body["drills"]),
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
