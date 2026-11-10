"""demo_geoexport.py — geoexportproof-demo@1.0.0: deterministic export of
the export-gate proof surface (ADR-0076).

Synthetic fixtures only; production machinery. Byte-for-byte
reproducible, sha256-stamped under the compact contract, registered in
proofindex, gated by the meta exporter-determinism suite.

Demo story: the estate had exactly ONE raw-GeoJSON site left —
households.to_geojson() hand-built its FeatureCollection, rounded
coordinates to 5 decimals WITHOUT saying so, and carried no CRS member,
no digest, no gate. It now flows through gisexport.collection() like
every other artifact, and the exporter itself grew teeth: collection()
range-validates every position in every feature BEFORE stamping, over a
closed geometry vocabulary, with the offender named — the checksum is
the contract, and the checksum presumes bytes that deserve it. The
households member states the privacy choice and the quantization AS
DATA: an identified export says so on its face. Five drills follow:
three fail closed, two pass with the basis stated — silence was the
defect, disclosure is the fix.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock", "gisexport", "households"):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))

import geolock  # noqa: E402
import gisexport  # noqa: E402
import households  # noqa: E402

DEMO_VERSION = "geoexportproof-demo@1.0.0"
CLOCK = "2026-11-11T17:00:00Z"
OUT = os.path.join(_HERE, "..", "..", "apps", "pwa", "geoexportproof.json")

# Synthetic households — not real people. KAWO is a fixture area name.
SYNTH_HOUSEHOLDS = [
    households.Household("HH-DEMO-%02d" % i, "KAWO-DEMO",
                         10.50 + 0.01 * i, 7.40 + 0.01 * i,
                         [households.Member("PT-DEMO-%02d" % i,
                                            "2026-09-01", "measles-2",
                                            "Synthetic Person")])
    for i in range(8)]


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _refusal(fn):
    try:
        fn()
    except Exception as exc:
        return {"outcome": "refused",
                "detail": "%s: %s" % (type(exc).__name__, exc)}
    return {"outcome": "accepted", "detail": "NO REFUSAL — DEFECT"}


def _register(hhs):
    reg = households.HouseholdRegister()
    for h in hhs:
        reg.register(h)
    return reg


def run_demo():
    registry = geolock.CRSRegistry()
    registry.register("EPSG:32631", kind="projected", projection="utm",
                      name="UTM 31N",
                      params={"zone": 31, "hemisphere": "N"})

    governed = _register(SYNTH_HOUSEHOLDS).to_geojson(
        as_of="2026-10-10", registry=registry)
    ungoverned = _register(SYNTH_HOUSEHOLDS).to_geojson(
        as_of="2026-10-10")
    identified = _register(SYNTH_HOUSEHOLDS).to_geojson(
        anonymize=False, as_of="2026-10-10", registry=registry)

    gl = governed["openhealth:geolock"]
    hh = governed["openhealth:households"]

    # Drill 2 machinery: tamper AFTER export, then recompute.
    tampered = json.loads(json.dumps(governed))
    tampered["features"][0]["properties"]["members"] = 99
    recomputed = _stamp(tampered["features"])

    control = {
        "census": {
            "raw_geojson_sites_before": 1,
            "raw_geojson_sites_after": 0,
            "method": "estate-wide grep for raw Feature/FeatureCollection "
                      "construction outside the governed path — one site "
                      "(households.to_geojson) found and adopted; apps JS "
                      "and CSV writers carry no coordinates"},
        "governed_export": {
            "features": len(governed["features"]),
            "governed": gl["governed"],
            "basis": gl["basis"],
            "coordinates_checked": gl["coordinates_checked"],
            "quantization": hh["coordinate_quantization"],
            "anonymized": hh["anonymized"],
            "features_sha256": gl["features_sha256"]},
        "privacy_floor": {
            "raw_ids_in_anonymized_export": any(
                "HH-DEMO" in str(f["properties"]) or
                "PT-DEMO" in str(f["properties"])
                for f in governed["features"]),
            "identified_export_states_it":
                identified["openhealth:households"]["anonymized"] is False},
    }

    drills = [
        {"name": "a metre-space household is registered",
         "attack": "EPSG:3857 coordinates as degrees at the door",
         "result": _refusal(
             lambda: _register([households.Household(
                 "HH-BAD", "KAWO-DEMO", 727867.6, 376170.8)]))},
        {"name": "a caller-built feature smuggles a metre-space point "
                 "past the layer helpers",
         "attack": "collection() with a hand-built EPSG:3857 Point",
         "result": _refusal(
             lambda: gisexport.collection(
                 [{"type": "Feature",
                   "geometry": {"type": "Point",
                                "coordinates": [376170.8, 727867.6]},
                   "properties": {}}], registry=registry))},
        {"name": "a geometry outside the closed vocabulary arrives",
         "attack": "GeometryCollection at the export gate",
         "result": _refusal(
             lambda: gisexport.collection(
                 [{"type": "Feature",
                   "geometry": {"type": "GeometryCollection",
                                "geometries": []},
                   "properties": {}}], registry=registry))},
        {"name": "a feature is tampered with after the stamp",
         "attack": "members 1 -> 99 post-export",
         "result": {"outcome": "refused",
                    "detail": "recomputed features digest %s… != stamped "
                              "%s… — the checksum catches it"
                              % (recomputed[:12],
                                 gl["features_sha256"][:12])}
                    if recomputed != gl["features_sha256"] else
                    {"outcome": "accepted",
                     "detail": "NO REFUSAL — DEFECT"}},
        {"name": "an export runs with no registry injected",
         "attack": "ungoverned planning-map export",
         "result": {"outcome": "stated",
                    "detail": "basis is %s and quantization is %s — the "
                              "default is disclosed on the artifact, not "
                              "assumed"
                              % (ungoverned["openhealth:geolock"]["basis"],
                                 "stated"
                                 if "5 decimal places" in
                                 ungoverned["openhealth:households"]
                                 ["coordinate_quantization"] else
                                 "MISSING — DEFECT")}},
        {"name": "a caller requests an identified export",
         "attack": "anonymize=False for a named worklist",
         "result": {"outcome": "stated",
                    "detail": "the artifact carries anonymized: false ON "
                              "ITS FACE — the privacy downgrade is data, "
                              "never silence (household id %s present by "
                              "explicit choice)"
                              % identified["features"][0]["properties"]
                              ["household"]}},
    ]

    body = {
        "surface": "geoexportproof",
        "demo": True,
        "banner": "Synthetic households only — the register below is "
                  "fixture, never a real person. The machinery exercised "
                  "— the export coordinate gate, the governed members, "
                  "the digest — is the production code.",
        "versions": {"geolock": geolock.GEOLOCK_VERSION,
                     "gisexport": gisexport.GIS_VERSION,
                     "households": households.HOUSEHOLDS_VERSION,
                     "exporter": DEMO_VERSION},
        "generated_at": CLOCK,
        "control": control,
        "drills": drills,
        "honesty": {
            "checksum_presumes_deserving_bytes": "the gate validates "
                "every coordinate BEFORE the stamp — a governed-looking "
                "digest over a metre-space feature was the hole",
            "defence_in_depth": "registration refuses bad coordinates "
                "with the offender named (first wall); the export gate "
                "re-checks caller-built features (second wall)",
            "privacy_as_data": "anonymization and 5dp quantization are "
                "foreign members on the artifact — a planner holding the "
                "file knows exactly what they are holding",
            "census_is_data": "raw-GeoJSON sites: 1 before, 0 after — "
                "the seam is adopted by its own estate, or it is "
                "decoration",
            "closed_vocabulary": "geometry types are a closed set; "
                "GeometryCollection is refused until a caller needs it "
                "— an ungated geometry is an unstamped one",
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
    print("geoexportproof: %d drills, %d positions checked, sha %s -> %s"
          % (len(body["drills"]),
             body["control"]["governed_export"]["coordinates_checked"],
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
