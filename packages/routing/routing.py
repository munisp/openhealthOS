"""Road-network routing and ETA from offline OSM layer extracts.

The dispatch ETA heuristic (haversine × 1.4 road factor) is fine for a demo
and wrong on a river, a lagoon, or a one-way expressway. Layercake (osmus,
CC0 code / ODbL data) publishes OSM as cloud-native thematic extracts; a
state office downloads the roads layer once, converts it to the compact
graph JSON this module reads, and dispatch computes ETAs on the actual road
network — fully offline.

Graph JSON format (produced by `build_graph_from_geojson`):
    {"nodes": {"n1": [lat, lng], ...},
     "edges": {"n1": {"n2": metres, ...}, ...},
     "speed_kmh": {"motorway": 80, "trunk": 60, ..., "default": 25},
     "crs_contract": {...}, "source_sha256": "..."}

Routing is A* with haversine heuristic — admissible, so results are exact.

routing@1.1.0 (ADR-0074) — the CRS gate is no longer fail-open:

- Every coordinate is range-validated at graph build. Metre-space
  coordinates (a Web-Mercator or UTM extract) used to sail through and
  produce a nonsense graph; now they are refused with the offending
  coordinate named. Reprojection is geolock's job — geolock.transform
  is the only road in.
- CRS declarations are honoured: a legacy pre-7946 ``crs`` member or an
  ``openhealth:geolock`` foreign member must declare WGS-84 lng/lat, or
  the build refuses. An undeclared extract still builds (RFC 7946) but
  the graph SAYS the basis was assumed — silence was the defect.
- An injected geolock CRSRegistry upgrades the basis to
  ``registry-governed``: the CRS must be registered and geographic.
  The registry is injected like a clock — this package never imports
  geolock; the seam is the registry protocol.
- The geodesy model is stated as data (GEODESY_CONTRACT), the source
  extract is pinned by digest, and every route carries a digest of its
  endpoints and result — the checksum is the contract, extended to
  routes.
"""

import hashlib
import heapq
import json
import math

ROUTING_VERSION = "routing@1.2.0"

EARTH_KM = 6371.0

DEFAULT_SPEEDS = {"motorway": 80, "trunk": 60, "primary": 50, "secondary": 40,
                  "tertiary": 30, "residential": 25, "unclassified": 20,
                  "track": 12, "default": 25}

#: The distance model, stated as data. Spherical haversine at the mean
#: radius errs ≤ ~0.3% against the WGS-84 geodesic — honest for ETA and
#: capacity planning, never presented as survey-grade.
GEODESY_CONTRACT = {
    "model": "spherical-haversine",
    "radius_km": EARTH_KM,
    "honest_error": "<=0.3% vs WGS-84 geodesic",
    "crs_meaning": "governed by geolock — degrees are WGS-84 lng/lat",
}

#: The only CRS this router accepts: RFC 7946 WGS-84, lng/lat order.
ROUTABLE_CRS = "EPSG:4326"


class Error(Exception):
    pass


def _canon(obj) -> str:
    """Compact canonical digest — the estate's primary contract."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def haversine_km(a, b):
    """Straight-line distance under GEODESY_CONTRACT. routing@1.2.0
    (ADR-0078): the sqrt argument is clamped to 1.0 — at exactly
    antipodal points float rounding can push it a hair above 1, and
    this copy was the estate's last UNCLAMPED Python haversine. The
    clamp is stated, value-identical everywhere the unclamped formula
    did not crash, and this function now sits in the geodesyproof
    convergence matrix — the drift guard pins BEHAVIOR, not just the
    constants."""
    (lat1, lng1), (lat2, lng2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(h)))


def _check_coordinate(lng, lat):
    """Degrees or nothing. A metre-space coordinate is a category error,
    refused with the coordinate named — never silently routed."""
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise Error(
            "coordinate out of range: %r, %r — metre-space coordinates "
            "are not routable degrees; reproject first "
            "(geolock.transform)" % (lng, lat))


def _crs_basis(roads_fc, registry):
    """Determine and validate the extract's CRS declaration. Returns the
    basis string for the graph's crs_contract. Refusals are named."""
    member = roads_fc.get("openhealth:geolock")
    legacy = roads_fc.get("crs")
    if legacy is not None:
        # Pre-RFC-7946 extracts may carry a crs member. Only WGS-84
        # declarations are routable; anything else must be reprojected.
        txt = json.dumps(legacy)
        if "CRS84" not in txt and "4326" not in txt:
            raise Error(
                "extract declares a non-WGS-84 CRS (%s) — reproject to "
                "WGS-84 lng/lat first (geolock.transform); degrees are "
                "the only routable input" % txt[:120])
        basis = "declared-legacy-crs84"
    elif member is not None:
        if member.get("crs") != ROUTABLE_CRS \
                or member.get("axis_order") != "lng,lat":
            raise Error(
                "geolock member declares %r/%r — the router accepts "
                "only EPSG:4326 lng,lat; reproject first"
                % (member.get("crs"), member.get("axis_order")))
        basis = "declared-governed" if member.get("governed") \
            else "declared"
    else:
        basis = "assumed-rfc-7946"
    if registry is not None:
        rec = registry.get(ROUTABLE_CRS)  # named refusal if unregistered
        if rec.get("kind") != "geographic":
            raise Error(
                "registered CRS %r is %s — routing needs a geographic "
                "CRS" % (ROUTABLE_CRS, rec.get("kind")))
        basis = "registry-governed"
    return basis


class RoadGraph:
    def __init__(self, graph):
        self.nodes = graph["nodes"]
        self.edges = graph["edges"]
        self.speeds = graph.get("speed_kmh", DEFAULT_SPEEDS)
        self.edge_speed = graph.get("edge_speed", {})  # "a|b" -> road class
        self.crs_contract = graph.get(
            "crs_contract", {"code": ROUTABLE_CRS, "axis_order": "lng,lat",
                             "basis": "legacy-unstated"})
        self.source_sha256 = graph.get("source_sha256")

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f))

    def nearest_node(self, lat, lng, max_km=2.0):
        best, best_d = None, max_km
        for nid, (nlat, nlng) in self.nodes.items():
            d = haversine_km((lat, lng), (nlat, nlng))
            if d < best_d:
                best, best_d = nid, d
        return best

    def route(self, from_lat, from_lng, to_lat, to_lng):
        """A* over the road graph. Returns {"distance_km", "eta_min",
        "path", "route_sha256"} — the digest pins endpoints and result."""
        _check_coordinate(from_lng, from_lat)
        _check_coordinate(to_lng, to_lat)
        start = self.nearest_node(from_lat, from_lng)
        goal = self.nearest_node(to_lat, to_lng)
        if start is None or goal is None:
            raise Error("no road node within reach of an endpoint")
        if start == goal:
            d = haversine_km((from_lat, from_lng), (to_lat, to_lng))
            return self._result(round(d, 2), round(d / 25 * 60, 1),
                                [start], from_lat, from_lng, to_lat, to_lng)

        g = {start: 0.0}
        prev = {start: None}
        goal_pos = self.nodes[goal]
        pq = [(haversine_km(self.nodes[start], goal_pos), start)]
        while pq:
            _, u = heapq.heappop(pq)
            if u == goal:
                break
            for v, metres in self.edges.get(u, {}).items():
                cand = g[u] + metres / 1000.0
                if cand < g.get(v, math.inf):
                    g[v] = cand
                    prev[v] = u
                    heapq.heappush(pq, (cand + haversine_km(self.nodes[v], goal_pos), v))
        if goal not in g:
            raise Error("no road path between endpoints")

        path, u = [], goal
        while u is not None:
            path.append(u)
            u = prev[u]
        path.reverse()
        # ETA from per-edge speed classes
        minutes = 0.0
        for a, b in zip(path, path[1:]):
            metres = self.edges[a][b]
            klass = self.edge_speed.get(f"{a}|{b}", "default")
            minutes += (metres / 1000.0) / self.speeds.get(klass, self.speeds["default"]) * 60
        return self._result(round(g[goal], 2), round(minutes, 1), path,
                            from_lat, from_lng, to_lat, to_lng)

    @staticmethod
    def _result(distance_km, eta_min, path,
                from_lat, from_lng, to_lat, to_lng):
        body = {"from": [from_lat, from_lng], "to": [to_lat, to_lng],
                "distance_km": distance_km, "eta_min": eta_min,
                "path": path}
        return {"distance_km": distance_km, "eta_min": eta_min,
                "path": path, "route_sha256": _canon(body)}


def build_graph_from_geojson(roads_fc, snap_m=25.0, speeds=None, *,
                             registry=None):
    """Convert a layercake-style roads GeoJSON (LineString features with a
    `highway` property) into the compact graph. Coordinates are snapped to a
    grid so segment endpoints join; speeds override DEFAULT_SPEEDS.

    CRS gate (ADR-0074): every coordinate is range-validated; declared
    CRSs must be WGS-84 lng/lat; an injected geolock registry upgrades
    the basis to registry-governed; the source extract is pinned by
    digest in the graph."""
    speeds = speeds or DEFAULT_SPEEDS
    grid = {}
    nodes, edges, edge_speed = {}, {}, {}

    feats = roads_fc.get("features", [])
    if roads_fc.get("type") != "FeatureCollection":
        raise Error("expected a GeoJSON FeatureCollection")
    basis = _crs_basis(roads_fc, registry)

    def snap(lat, lng):
        key = (round(lat, 6), round(lng, 6))
        if key not in grid:
            nid = f"n{len(grid)}"
            grid[key] = nid
            nodes[nid] = [lat, lng]
            edges[nid] = {}
        return grid[key]

    for feat in feats:
        geom = feat.get("geometry", {})
        if geom.get("type") != "LineString":
            continue  # MultiLineString support: split into parts upstream
        klass = feat.get("properties", {}).get("highway", "default")
        if klass not in speeds:
            klass = "default"
        coords = geom["coordinates"]
        for (lng1, lat1), (lng2, lat2) in zip(coords, coords[1:]):
            _check_coordinate(lng1, lat1)
            _check_coordinate(lng2, lat2)
            a, b = snap(lat1, lng1), snap(lat2, lng2)
            if a == b:
                continue
            metres = haversine_km((lat1, lng1), (lat2, lng2)) * 1000.0
            if metres < snap_m / 10:
                continue  # snap noise
            edges[a][b] = min(edges[a].get(b, math.inf), metres)
            edges[b][a] = min(edges[b].get(a, math.inf), metres)
            edge_speed[f"{a}|{b}"] = klass
            edge_speed[f"{b}|{a}"] = klass
    if not nodes:
        raise Error("no usable LineString features in the extract")
    return {"nodes": nodes, "edges": edges, "speed_kmh": speeds,
            "edge_speed": edge_speed,
            "crs_contract": {"code": ROUTABLE_CRS, "axis_order": "lng,lat",
                             "basis": basis,
                             "geodesy": dict(GEODESY_CONTRACT)},
            "source_sha256": _canon(feats)}
