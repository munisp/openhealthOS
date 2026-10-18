"""GeoJSON export for offline GIS planning (the RustGIS/any-QGIS companion).

The platform stays boring at runtime; spatial *analysis* (where to build the
next PHC, which catchments are underserved, ambulance response-time heat)
happens offline in a GIS workbench. This package is the bridge: it emits
standards-compliant RFC 7946 GeoJSON FeatureCollections that RustGIS, QGIS,
or any GIS tool opens directly.

All exports are k-anonymised by default for patient-derived layers — the
same doctrine as surveillance (ADR-0020): planners need density, not names.
"""

import math

K_ANONYMITY = 5  # suppress cells below this count


class Error(Exception):
    pass


def _feature(lng, lat, properties):
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise Error(f"coordinate out of range: {lng},{lat}")
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": properties}


def collection(features):
    return {"type": "FeatureCollection", "features": features}


def facilities_layer(facilities):
    """facilities: [{id, name, kind, lat, lng, beds, services:[...]}]"""
    feats = [_feature(f["lng"], f["lat"],
                      {"id": f["id"], "name": f["name"], "kind": f["kind"],
                       "beds": f.get("beds", 0),
                       "services": ";".join(f.get("services", []))})
             for f in facilities]
    return collection(feats)


def households_density_layer(households, cell_deg=0.05, k=K_ANONYMITY):
    """Aggregate households into grid cells (default ~5 km) with k-anonymity.
    households: [{lat, lng, members}] — raw points NEVER leave this layer."""
    cells = {}
    for h in households:
        key = (math.floor(h["lat"] / cell_deg), math.floor(h["lng"] / cell_deg))
        c = cells.setdefault(key, {"households": 0, "members": 0,
                                   "lats": [], "lngs": []})
        c["households"] += 1
        c["members"] += h.get("members", 1)
        c["lats"].append(h["lat"])
        c["lngs"].append(h["lng"])
    feats = []
    for (ilat, ilng), c in sorted(cells.items()):
        if c["households"] < k:
            continue  # k-anonymity: sparse cells are suppressed entirely
        feats.append(_feature(
            sum(c["lngs"]) / len(c["lngs"]), sum(c["lats"]) / len(c["lats"]),
            {"households": c["households"], "members": c["members"],
             "cell": f"{ilat}_{ilng}"}))
    return collection(feats)


def response_time_layer(incidents, cell_deg=0.05, k=K_ANONYMITY):
    """incidents: [{lat, lng, response_min}] -> mean response per cell,
    k-anonymised. This is the layer planners overlay on population density
    to site new ambulance posts."""
    cells = {}
    for inc in incidents:
        key = (math.floor(inc["lat"] / cell_deg), math.floor(inc["lng"] / cell_deg))
        cells.setdefault(key, []).append(inc["response_min"])
    feats = []
    for (ilat, ilng), times in sorted(cells.items()):
        if len(times) < k:
            continue
        feats.append(_feature(
            (ilng + 0.5) * cell_deg, (ilat + 0.5) * cell_deg,
            {"incidents": len(times),
             "mean_response_min": round(sum(times) / len(times), 1),
             "worst_response_min": max(times),
             "cell": f"{ilat}_{ilng}"}))
    return collection(feats)


def coverage_gap_layer(facilities, households, max_km=5.0, k=K_ANONYMITY):
    """Households farther than max_km from ANY facility — the classic
    'where is care out of reach' layer. k-anonymised grid output."""
    def hav(a_lat, a_lng, b_lat, b_lng):
        r = 6371.0
        p1, p2 = math.radians(a_lat), math.radians(b_lat)
        dp, dl = math.radians(b_lat - a_lat), math.radians(b_lng - a_lng)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    far = []
    for h in households:
        nearest = min((hav(h["lat"], h["lng"], f["lat"], f["lng"])
                       for f in facilities), default=None)
        if nearest is None or nearest > max_km:
            far.append({**h, "distance_km": nearest})
    cells = {}
    for h in far:
        key = (math.floor(h["lat"] / 0.05), math.floor(h["lng"] / 0.05))
        cells.setdefault(key, []).append(h)
    feats = []
    for (ilat, ilng), hs in sorted(cells.items()):
        if len(hs) < k:
            continue
        feats.append(_feature(
            (ilng + 0.5) * 0.05, (ilat + 0.5) * 0.05,
            {"unserved_households": len(hs),
             "members": sum(h.get("members", 1) for h in hs),
             "mean_distance_km": round(
                 sum(h["distance_km"] for h in hs) / len(hs), 1)}))
    return collection(feats)
