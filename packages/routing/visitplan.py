"""CHW visit route planning — nearest-neighbour seed + 2-opt improvement
(innovation 5).

A community health worker with 20 household visits and a bicycle should not
walk the list in registration order. Given the day's visits and a start
point (usually the PHC), produce an ordered route that minimises total
travel: nearest-neighbour for the seed tour, then 2-opt exchanges until no
improvement — optimal enough at PHC scale (N ≤ a few hundred).

Distances: geolock.distance_km × road factor by default (offline, zero
data); when a RoadGraph is available the caller can pass a dist function
backed by real roads. Returns the ordered visits plus total km — honest
arithmetic the CHW supervisor can eyeball on paper.

ADR-0075: the private haversine is retired; geodesy is geolock's. The
ADR-0074 line stands for the ROUTER (routing.py injects its registry,
never imports geolock) — visitplan imports only a pure function and a
constant, which is arithmetic, not an engine.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "geolock"))
import geolock  # noqa: E402

VISITPLAN_VERSION = "visitplan@1.1.0"
ROAD_FACTOR = 1.4  # same heuristic as dispatch ETA when no road graph

#: Stated basis carried on every plan — the supervisor's paper check
#: deserves to know what arithmetic it is checking.
GEODESY_BASIS = "geolock-geodesy-contract"


@dataclass
class Visit:
    id: str
    lat: float
    lng: float
    label: str = ""


def haversine_km(a_lat, a_lng, b_lat, b_lng) -> float:
    """Delegates to geolock.distance_km (ADR-0075) — one contract, one
    function. Kept under its historic name for callers and tests."""
    return geolock.distance_km(a_lat, a_lng, b_lat, b_lng)


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
        return {"order": [], "total_km": 0.0, "legs": [],
                "geodesy": {"basis": GEODESY_BASIS,
                            "road_factor": ROAD_FACTOR,
                            "contract": geolock.GEODESY_CONTRACT}}
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
                       for v in tour],
            "geodesy": {"basis": ("road-graph-with-geodesy-fallback"
                                  if graph_route else GEODESY_BASIS),
                        "road_factor": ROAD_FACTOR,
                        "contract": geolock.GEODESY_CONTRACT}}
