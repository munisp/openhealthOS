"""CHW visit route planning — nearest-neighbour seed + 2-opt improvement
(innovation 5).

A community health worker with 20 household visits and a bicycle should not
walk the list in registration order. Given the day's visits and a start
point (usually the PHC), produce an ordered route that minimises total
travel: nearest-neighbour for the seed tour, then 2-opt exchanges until no
improvement — optimal enough at PHC scale (N ≤ a few hundred).

Distances: haversine × road factor by default (offline, zero data); when a
RoadGraph is available the caller can pass a dist function backed by real
roads. Returns the ordered visits plus total km — honest arithmetic the CHW
supervisor can eyeball on paper.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_KM = 6371.0
ROAD_FACTOR = 1.4  # same heuristic as dispatch ETA when no road graph


@dataclass
class Visit:
    id: str
    lat: float
    lng: float
    label: str = ""


def haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp = math.radians(b_lat - a_lat)
    dl = math.radians(b_lng - a_lng)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(h))


def _dist_fn(graph_route=None):
    """Default: haversine × road factor. With a graph: real road metres."""
    if graph_route is None:
        return lambda a, b: haversine_km(a.lat, a.lng, b.lat, b.lng) * ROAD_FACTOR

    def road(a, b):
        r = graph_route(a.lat, a.lng, b.lat, b.lng)
        metres = r.get("distance_m") if isinstance(r, dict) else None
        if metres is None:
            return haversine_km(a.lat, a.lng, b.lat, b.lng) * ROAD_FACTOR
        return metres / 1000.0
    return road


def plan_visits(visits: list[Visit], start_lat: float, start_lng: float,
                graph_route=None) -> dict:
    """Order the visits, returning {order, total_km, legs}.

    order: [visit ids] in visit sequence (start point excluded, it is not
    a visit); legs: per-hop km for the supervisor's paper check.
    """
    if not visits:
        return {"order": [], "total_km": 0.0, "legs": []}
    dist = _dist_fn(graph_route)
    start = Visit("__start__", start_lat, start_lng)

    # --- nearest-neighbour seed ---
    remaining = list(visits)
    tour: list[Visit] = []
    cur = start
    while remaining:
        nxt = min(remaining, key=lambda v: dist(cur, v))
        tour.append(nxt)
        remaining.remove(nxt)
        cur = nxt

    def tour_km(t: list[Visit]) -> float:
        pts = [start] + t
        return sum(dist(a, b) for a, b in zip(pts, pts[1:]))

    # --- 2-opt improvement ---
    improved = True
    best = tour_km(tour)
    while improved:
        improved = False
        for i in range(len(tour) - 1):
            for j in range(i + 1, len(tour)):
                cand = tour[:i] + tour[i:j + 1][::-1] + tour[j + 1:]
                c = tour_km(cand)
                if c < best - 1e-9:
                    tour, best = cand, c
                    improved = True

    pts = [start] + tour
    legs = [round(dist(a, b), 2) for a, b in zip(pts, pts[1:])]
    return {"order": [v.id for v in tour], "total_km": round(best, 2),
            "legs": legs,
            "visits": [{"id": v.id, "lat": v.lat, "lng": v.lng, "label": v.label}
                       for v in tour]}
