"""geolock@1.0.0 — coordinate-reference governance: CRSs as data,
projections pinned by known-answer tests, coordinates under contract.

The estate already exports GeoJSON (gisexport, RFC 7946, k-anonymised)
and routes on OSM graphs (routing, A* haversine). What it never had is
the CRS layer between coordinates and meaning: everything silently
assumed WGS-84 degrees. Degrees are not distances: an area computed in
degrees is folklore, a catchment measured on the wrong ellipsoid is a
planning error with a budget attached.

This is the clean-room answer to GDAL/PROJ and to pure-Rust ports such
as opengeos/terrio (MIT): heavy raster/COG/point-cloud work is NEVER
embedded here (ADR-0005) — it composes through ratified template
adapters with injected executors, like ledgeranchor's notaries and
kgraph's SPARQL endpoints. What lives here is the governed core:
the projections are standard published formulas (USGS/PROJ), and like
terrio's doctrine — tested against PROJ, not trusting it blindly —
every projection is pinned by KNOWN-ANSWER vectors checked against
PROJ at pin time, with the tolerance stated.

Doctrines enforced here:

- A CRS is a registry entry, never a code branch. Transforming to or
  from an unregistered CRS refuses, named. WGS-84 and Web Mercator
  ship seeded; UTM zones are deployment-registered (country-agnostic:
  the zone follows the coordinate, not the codebase).
- A coordinate without a CRS is folklore: every point record carries
  its CRS and its digest. Range validation happens at the door.
- Area in degrees is refused: area_km2 demands a projected CRS.
- Drift is a verdict, never silent: verify_roundtrip returns green
  within the stated tolerance and red with the drift named beyond it.
- Known answers are data: KNOWN_ANSWERS pins control points (verified
  against PROJ 3.7.2 at pin time) with a stated tolerance; a formula
  that drifts from its own pinned vectors fails loudly.
- Adapters fail closed: terrio-cli / gdal are template entries that
  refuse until two distinct named approvers ratify them, and then run
  only through an injected executor. A missing wire is stated, never
  faked.

Stdlib only (ADR-0005). No clocks are read here — the caller stamps
time.
"""

from __future__ import annotations

import hashlib
import json
import math

GEOLOCK_VERSION = "geolock@1.1.0"

#: The estate's geodesy contract, stated as data (ADR-0075). Before this
#: constant, five packages each re-implemented spherical haversine with
#: two different earth radii (6371.0 and the IUGG mean 6371.0088) and no
#: stated basis. One contract now owns the arithmetic; every consumer
#: either delegates here or is pinned equal to this by the drift-guard
#: (packages/geodesy). The 1.381 ppm between the retired IUGG mean and
#: this radius is absorbed by the stated honesty band — 0.3% (3000 ppm)
#: is more than 2000x wider than the correction, so no stamped surface
#: moves.
GEODESY_CONTRACT = {
    "model": "spherical-haversine",
    "radius_km": 6371.0,
    "honest_error": "<=0.3% vs WGS-84 geodesic",
    "never": "survey-grade — property lines and engineering need a geodesist",
    "crs_meaning": "degrees are WGS-84 lng/lat — CRS governance lives in this package",
    "supersedes": "five private haversines, radii 6371.0 and 6371.0088 (ADR-0075)",
}

#: Closed projection vocabulary. A projection not listed here is
#: refused at CRS registration — transforms never guess.
PROJECTIONS = ("longlat", "web-mercator", "utm")

#: WGS-84 ellipsoid constants (public standard).
_A = 6378137.0
_F = 1 / 298.257223563
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)
_K0 = 0.9996
_R_MINOR = 6356752.314245179

#: Known-answer vectors, verified against PROJ 3.7.2 at pin time.
#: (src, dst, x, y, expected_x, expected_y) — tolerance stated below.
KNOWN_ANSWERS = [
    ("EPSG:4326", "EPSG:3857", 3.3792, 6.5244,
     376170.82328863005, 727867.6201716245),
    ("EPSG:4326", "EPSG:32631", 3.3792, 6.5244,
     541924.3013987611, 721189.2172458889),
    ("EPSG:4326", "EPSG:32632", 7.3986, 9.0765,
     323998.9537201617, 1003697.9049219984),
    ("EPSG:4326", "EPSG:32734", 18.4241, -33.9249,
     261881.59852399467, 6243182.354517815),
    ("EPSG:4326", "EPSG:32627", -23.5133, 14.9330,
     229641.82702784915, 1652444.2462634032),
    ("EPSG:4326", "EPSG:3857", 0.0, 85.05112878,
     0.0, 20037508.34303882),
]

#: Tolerance for known-answer verification, in output units (metres
#: for projected targets, degrees are never a KAT target). Stated,
#: never implied.
KAT_TOLERANCE = 0.01

#: Roundtrip tolerance in metres — a roundtrip that drifts further is
#: a defect, named red.
ROUNDTRIP_TOLERANCE_M = 0.001

#: External heavy-I/O adapters. Template only: ratify twice, inject
#: an executor. The engine itself is never embedded (ADR-0005).
ADAPTERS = {
    "terrio-cli": {
        "status": "template",
        "note": "opengeos/terrio (MIT) pure-Rust raster/vector/"
                "point-cloud I/O — GDAL-style CLI, COG/PMTiles/Zarr, "
                "EPSG engine. Runs out-of-process via injected "
                "executor after two named approvers ratify.",
        "approvals": [],
    },
    "gdal-cli": {
        "status": "template",
        "note": "GDAL/OGR utilities for the long tail terrio does "
                "not cover. Template only.",
        "approvals": [],
    },
}


class GeolockError(ValueError):
    """A malformed CRS, coordinate, or transform. Fail closed, always
    named."""


def _canon(obj) -> str:
    """Compact canonical digest — the estate's primary contract."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


# --- projections: standard published formulas (USGS/PROJ) ----------

def _merc_forward(lng, lat):
    if not (-90.0 < lat < 90.0):
        raise GeolockError(
            "latitude %r outside the open interval (-90, 90) — the "
            "poles are not Web Mercator" % lat)
    x = _A * math.radians(lng)
    lat = max(min(lat, 85.05112878), -85.05112878)
    y = _A * math.log(math.tan(math.pi / 4
                               + math.radians(lat) / 2))
    return x, y


def _merc_inverse(x, y):
    lng = math.degrees(x / _A)
    lat = math.degrees(2 * math.atan(math.exp(y / _A)) - math.pi / 2)
    return lng, lat


def _utm_params(params):
    try:
        zone = int(params["zone"])
        hemi = params["hemisphere"]
    except (KeyError, TypeError, ValueError):
        raise GeolockError(
            "a UTM CRS needs zone (1-60) and hemisphere (N/S) — "
            "absence is not a value")
    if not 1 <= zone <= 60:
        raise GeolockError("UTM zone %r outside 1-60" % (zone,))
    if hemi not in ("N", "S"):
        raise GeolockError("UTM hemisphere must be N or S")
    return zone, hemi


def _meridional_arc(phi):
    e4 = _E2 ** 2
    e6 = _E2 ** 3
    return _A * ((1 - _E2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * phi
                 - (3 * _E2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024)
                 * math.sin(2 * phi)
                 + (15 * e4 / 256 + 45 * e6 / 1024)
                 * math.sin(4 * phi)
                 - (35 * e6 / 3072) * math.sin(6 * phi))


def _utm_forward(lng, lat, *, zone, hemisphere):
    phi = math.radians(lat)
    lam0 = math.radians((zone - 1) * 6 - 180 + 3)
    lam = math.radians(lng)
    sinp, cosp, tanp = math.sin(phi), math.cos(phi), math.tan(phi)
    n = _A / math.sqrt(1 - _E2 * sinp * sinp)
    t = tanp * tanp
    c = _EP2 * cosp * cosp
    a = cosp * (lam - lam0)
    m = _meridional_arc(phi)
    e = 500000.0 + _K0 * n * (
        a + (1 - t + c) * a ** 3 / 6
        + (5 - 18 * t + t * t + 72 * c - 58 * _EP2) * a ** 5 / 120)
    nrt = _K0 * (m + n * tanp * (
        a * a / 2 + (5 - t + 9 * c + 4 * c * c) * a ** 4 / 24
        + (61 - 58 * t + t * t + 600 * c - 330 * _EP2) * a ** 6
        / 720))
    if hemisphere == "S":
        nrt += 10000000.0
    return e, nrt


def _utm_inverse(e, nrt, *, zone, hemisphere):
    if hemisphere == "S":
        nrt -= 10000000.0
    x = e - 500000.0
    m = nrt / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64
                    - 5 * _E2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32)
            * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = _EP2 * cos1 * cos1
    t1 = tan1 * tan1
    n1 = _A / math.sqrt(1 - _E2 * sin1 * sin1)
    r1 = _A * (1 - _E2) / (1 - _E2 * sin1 * sin1) ** 1.5
    d = x / (n1 * _K0)
    lat = phi1 - (n1 * tan1 / r1) * (
        d * d / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * _EP2) * d ** 4
        / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * _EP2
           - 3 * c1 * c1) * d ** 6 / 720)
    lam0 = math.radians((zone - 1) * 6 - 180 + 3)
    lng = lam0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                  + (5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * _EP2
                     + 24 * t1 * t1) * d ** 5 / 120) / cos1
    return math.degrees(lng), math.degrees(lat)


class CRSRegistry:
    """Coordinate reference systems as data. WGS-84 and Web Mercator
    ship seeded; everything else is deployment-registered."""

    def __init__(self, *, seeded=True):
        self.systems = {}
        if seeded:
            self.register("EPSG:4326", kind="geographic",
                          projection="longlat", name="WGS 84")
            self.register("EPSG:3857", kind="projected",
                          projection="web-mercator",
                          name="Web Mercator")

    def register(self, code, *, kind, projection, name,
                 params=None):
        if not isinstance(code, str) or not code:
            raise GeolockError("a CRS needs a code")
        if code in self.systems:
            raise GeolockError(
                "duplicate CRS %r — a registry, not a rewrite" % code)
        if kind not in ("geographic", "projected"):
            raise GeolockError(
                "unknown CRS kind %r — closed vocabulary" % (kind,))
        if projection not in PROJECTIONS:
            raise GeolockError(
                "unknown projection %r — the vocabulary is closed; "
                "heavier engines compose through adapters"
                % (projection,))
        if kind == "geographic" and projection != "longlat":
            raise GeolockError(
                "a geographic CRS is longlat — anything else is a "
                "category error")
        if projection == "utm":
            zone, hemi = _utm_params(params or {})
            params = {"zone": zone, "hemisphere": hemi}
        rec = {"code": code, "kind": kind, "projection": projection,
               "name": name or code, "params": params or {}}
        self.systems[code] = rec
        return dict(rec)

    def get(self, code):
        rec = self.systems.get(code)
        if rec is None:
            raise GeolockError(
                "unregistered CRS %r — absence is stated, never "
                "faked" % code)
        return dict(rec)


def _to_longlat(rec, x, y):
    proj = rec["projection"]
    if proj == "longlat":
        if not (-180 <= x <= 180 and -90 <= y <= 90):
            raise GeolockError(
                "coordinate out of range: %r, %r — folklore refused "
                "at the door" % (x, y))
        return x, y
    if proj == "web-mercator":
        return _merc_inverse(x, y)
    if proj == "utm":
        zone, hemi = rec["params"]["zone"], \
            rec["params"]["hemisphere"]
        return _utm_inverse(x, y, zone=zone, hemisphere=hemi)
    raise GeolockError("unreachable — closed vocabulary")


def _from_longlat(rec, lng, lat):
    proj = rec["projection"]
    if proj == "longlat":
        return lng, lat
    if proj == "web-mercator":
        return _merc_forward(lng, lat)
    if proj == "utm":
        zone, hemi = rec["params"]["zone"], \
            rec["params"]["hemisphere"]
        return _utm_forward(lng, lat, zone=zone, hemisphere=hemi)
    raise GeolockError("unreachable — closed vocabulary")


def point(registry, crs, x, y, *, subject=None):
    """A coordinate under contract: CRS named, ranges validated at
    the door, digest pinned."""
    registry.get(crs)   # named refusal if unregistered
    rec = registry.systems[crs]
    if rec["kind"] == "geographic" \
            and not (-180 <= x <= 180 and -90 <= y <= 90):
        raise GeolockError(
            "coordinate out of range: %r, %r" % (x, y))
    body = {"crs": crs, "x": x, "y": y, "subject": subject}
    body["sha256"] = _canon(body)
    return body


def transform(registry, src, dst, x, y):
    """Transform a coordinate between registered CRSs. Returns a
    record with input and output digests — the checksum is the
    contract, extended to coordinates."""
    s, d = registry.get(src), registry.get(dst)
    lng, lat = _to_longlat(s, x, y)
    ox, oy = _from_longlat(d, lng, lat)
    return {"src": src, "dst": dst,
            "input": {"x": x, "y": y}, "output": {"x": ox, "y": oy},
            "input_sha256": _canon({"crs": src, "x": x, "y": y}),
            "output_sha256": _canon({"crs": dst, "x": ox, "y": oy})}


def utm_zone_for(lng):
    """The UTM zone a longitude belongs to — the zone follows the
    coordinate, not the codebase."""
    if not -180 <= lng <= 180:
        raise GeolockError("longitude %r out of range" % (lng,))
    return min(int((lng + 180) // 6) + 1, 60)


def distance_km(lat1, lng1, lat2, lng2):
    """Great-circle distance in km under GEODESY_CONTRACT (ADR-0075).

    Inputs are WGS-84 degrees, lng/lat axis order per the estate's CRS
    contract. Metre-space coordinates are not degrees — range-check them
    with point() before measuring, or a projected extract will silently
    produce a nonsense distance (that failure mode is why the router
    range-checks at graph build and at route time).

    The sqrt argument is clamped to 1.0: at exactly antipodal points
    floating-point rounding can push it a hair above 1, and the estate's
    retired private haversines would have raised a domain error there.
    The clamp is stated, and value-identical everywhere the unclamped
    formula did not crash.
    """
    for name, v, lo, hi in (("lat1", lat1, -90, 90), ("lat2", lat2, -90, 90),
                            ("lng1", lng1, -180, 180), ("lng2", lng2, -180, 180)):
        if not lo <= v <= hi:
            raise GeolockError(
                "%s %r out of range — degrees are WGS-84 lng/lat; "
                "metre-space coordinates are not distances waiting to "
                "happen, reproject first (geolock.transform)" % (name, v))
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * GEODESY_CONTRACT["radius_km"] * math.asin(min(1.0, math.sqrt(a)))


def area_km2(registry, crs, ring):
    """Polygon area in km². Refuses a geographic CRS: area in
    degrees is folklore. The ring is a list of (x, y) in a projected
    CRS."""
    rec = registry.get(crs)
    if rec["kind"] != "projected":
        raise GeolockError(
            "area in degrees is folklore — register a projected CRS "
            "(e.g. the UTM zone of the polygon) first")
    if len(ring) < 3:
        raise GeolockError("a polygon needs at least 3 vertices")
    acc = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        acc += x1 * y2 - x2 * y1
    return abs(acc) / 2 / 1e6


def verify_roundtrip(registry, crs, x, y, *,
                     tolerance_m=ROUNDTRIP_TOLERANCE_M):
    """Transform away and back; drift beyond the stated tolerance is
    red and named, never silent."""
    rec = registry.get(crs)
    if rec["projection"] == "longlat":
        via = "EPSG:3857"
    else:
        via = "EPSG:4326"
    away = transform(registry, crs, via, x, y)
    back = transform(registry, via, crs,
                     away["output"]["x"], away["output"]["y"])
    dx = back["output"]["x"] - x
    dy = back["output"]["y"] - y
    drift = math.hypot(dx, dy)
    if drift > tolerance_m:
        return {"status": "drift", "severity": "red", "crs": crs,
                "drift_m": drift, "tolerance_m": tolerance_m,
                "detail": "roundtrip drift beyond tolerance — the "
                          "formula moved, named not hidden"}
    return {"status": "pinned", "severity": "green", "crs": crs,
            "drift_m": drift, "tolerance_m": tolerance_m,
            "detail": "roundtrip within tolerance"}


def verify_known_answers(registry, *, tolerance=KAT_TOLERANCE):
    """Check every pinned control vector against the live formulas.
    A formula that drifts from its own pinned vectors is a defect,
    named per vector."""
    results = []
    for src, dst, x, y, ex, ey in KNOWN_ANSWERS:
        try:
            r = transform(registry, src, dst, x, y)
            err = math.hypot(r["output"]["x"] - ex,
                             r["output"]["y"] - ey)
            results.append({
                "src": src, "dst": dst, "x": x, "y": y,
                "error": err, "tolerance": tolerance,
                "status": "pinned" if err <= tolerance else "drift"})
        except GeolockError as exc:
            results.append({"src": src, "dst": dst, "x": x, "y": y,
                            "status": "refused", "detail": str(exc)})
    ok = all(r["status"] == "pinned" for r in results)
    return {"status": "pinned" if ok else "drift",
            "tolerance": tolerance, "vectors": results}


class AdapterRegistry:
    """External heavy-I/O engines (terrio-cli, gdal-cli) as template
    adapters: two distinct named approvers ratify, an injected
    executor carries the wire, a missing or failing wire is stated
    NOT RUN — never faked."""

    def __init__(self, *, adapters=None):
        self.adapters = {k: {**v, "approvals": list(v["approvals"])}
                         for k, v in (adapters or ADAPTERS).items()}

    def ratify(self, adapter, *, by):
        a = self.adapters.get(adapter)
        if a is None:
            raise GeolockError(
                "unknown adapter %r — the registry is closed"
                % (adapter,))
        if not by:
            raise GeolockError("ratification needs a named approver")
        if by in a["approvals"]:
            raise GeolockError(
                "duplicate approver — four eyes means four eyes")
        a["approvals"].append(by)
        if len(set(a["approvals"])) >= 2:
            a["status"] = "ratified"
        return {"adapter": adapter, "status": a["status"],
                "approvals": list(a["approvals"])}

    def run(self, adapter, command, *, executor=None,
            executed_at=None):
        """Run an external engine command. executor(command) -> dict
        result. Refusals and failures are verdicts."""
        a = self.adapters.get(adapter)
        if a is None:
            raise GeolockError(
                "unknown adapter %r" % (adapter,))
        base = {"adapter": adapter,
                "command_sha256": _canon(command),
                "executed_at": executed_at}
        if a["status"] != "ratified":
            return {**base, "status": "refused",
                    "detail": "adapter is %s — a template engine "
                              "carries no clinical geometry until "
                              "two named approvers ratify it"
                              % a["status"]}
        if executor is None:
            return {**base, "status": "not-run",
                    "detail": "no executor injected — a missing wire "
                              "is stated, never faked"}
        try:
            result = executor(command)
        except Exception as exc:
            return {**base, "status": "not-run",
                    "detail": "executor error: %s: %s"
                    % (type(exc).__name__, exc)}
        if not isinstance(result, dict) or "sha256" not in result:
            return {**base, "status": "not-run",
                    "detail": "executor returned an unstamped "
                              "result — an unpinnable output is "
                              "unauditable"}
        return {**base, "status": "ok", "result": result}
