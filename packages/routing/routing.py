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
     "speed_kmh": {"motorway": 80, "trunk": 60, ..., "default": 25}}

Routing is A* with haversine heuristic — admissible, so results are exact.
"""

import heapq
import json
import math

EARTH_KM = 6371.0

DEFAULT_SPEEDS = {"motorway": 80, "trunk": 60, "primary": 50, "secondary": 40,
                  "tertiary": 30, "residential": 25, "unclassified": 20,
                  "track": 12, "default": 25}


class Error(Exception):
    pass


def haversine_km(a, b):
    (lat1, lng1), (lat2, lng2) = a, b
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


class RoadGraph:
    def __init__(self, graph):
        self.nodes = graph["nodes"]
        self.edges = graph["edges"]
        self.speeds = graph.get("speed_kmh", DEFAULT_SPEEDS)
        self.edge_speed = graph.get("edge_speed", {})  # "a|b" -> road class

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
        """A* over the road graph. Returns {"distance_km", "eta_min", "path"}."""
        start = self.nearest_node(from_lat, from_lng)
        goal = self.nearest_node(to_lat, to_lng)
        if start is None or goal is None:
            raise Error("no road node within reach of an endpoint")
        if start == goal:
            d = haversine_km((from_lat, from_lng), (to_lat, to_lng))
            return {"distance_km": round(d, 2), "eta_min": round(d / 25 * 60, 1),
                    "path": [start]}

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
        return {"distance_km": round(g[goal], 2), "eta_min": round(minutes, 1),
                "path": path}


def build_graph_from_geojson(roads_fc, snap_m=25.0, speeds=None):
    """Convert a layercake-style roads GeoJSON (LineString features with a
    `highway` property) into the compact graph. Coordinates are snapped to a
    grid so segment endpoints join; speeds override DEFAULT_SPEEDS."""
    speeds = speeds or DEFAULT_SPEEDS
    grid = {}
    nodes, edges, edge_speed = {}, {}, {}

    def snap(lat, lng):
        key = (round(lat, 6), round(lng, 6))
        if key not in grid:
            nid = f"n{len(grid)}"
            grid[key] = nid
            nodes[nid] = [lat, lng]
            edges[nid] = {}
        return grid[key]

    feats = roads_fc.get("features", [])
    if roads_fc.get("type") != "FeatureCollection":
        raise Error("expected a GeoJSON FeatureCollection")
    for feat in feats:
        geom = feat.get("geometry", {})
        if geom.get("type") != "LineString":
            continue  # MultiLineString support: split into parts upstream
        klass = feat.get("properties", {}).get("highway", "default")
        if klass not in speeds:
            klass = "default"
        coords = geom["coordinates"]
        for (lng1, lat1), (lng2, lat2) in zip(coords, coords[1:]):
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
            "edge_speed": edge_speed}
