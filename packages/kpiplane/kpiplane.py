"""KPI plane (ADR-0033) — the nightly state-plane batch job that turns
lakehouse gold + the ML registry into ONE document a hospital manager
or state desk reads every morning:

    lakehouse/gold/kpi_plane.json
      ├── facility_economics   margins / panel P&L / denials (ADR-0032)
      ├── panel_risk           enrolled panel by risk band
      ├── catchments           nearest-facility assignment + travel stats
      └── ml_health            champion versions, holdout metrics, drift PSI

Every section degrades independently to {"status": "no-data", ...} when
its input is absent — a pilot state with no enrolment export still gets
denial and drift views. Stdlib-only, single process, minutes at state
scale: the job is deliberately boring so it runs on the state plane next
to the event consumers (same doctrine as packages/lakehouse).

SEDONA / SPARK SWAP POINT (ADR-0014): `assign_catchments()` is an
O(members × facilities) nearest-neighbour pass. Past ~1M members this
moves to the batch tier as a Sedona spatial join
(ST_Distance / KNN join over ST_Point) with the member and facility
layers read from Parquet; the JSON contract written here does not
change, so the dashboards and this job's tests keep passing against the
Spark output. Ray is not involved — nothing here is training.

CLI:
    python3 kpiplane.py --lakehouse DIR [--facilities FILE]
        [--members FILE] [--enrollees FILE] [--encounters FILE]
        [--claims FILE] [--claims-reference FILE] [--registry DIR]
        [--capitation-kobo N] [--out FILE]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone

# --- sibling-package wiring (same seam as packages/economics) -----------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
for _rel in ("packages/economics", "packages/benefits", "ml"):
    _p = os.path.join(_REPO, _rel)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

EARTH_RADIUS_KM = 6371.0088  # IUGG mean — matches ml/openhealth_ml/catchment.py

BAND_ORDER = ["under5", "adult", "older-adult", "elderly", "maternal", "chronic"]


def _read_json(path):
    with open(path) as fh:
        return json.load(fh)


def _read_jsonl(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _nodata(section, reason):
    return {"status": "no-data", "section": section, "reason": reason}


# ---------------------------------------------------------------- economics
def facility_economics(encounters_path, enrollees_path, claims_path,
                       capitation_kobo):
    """Margins + panel P&L + denial classes from packages/economics.

    Encounters carry code/facility/staff_minutes/consumables_kobo/los_days;
    enrollees carry the fields economics.risk_band reads; claims carry the
    fields denial_report reads. Any missing input drops only its block.
    """
    try:
        import economics as E
    except ImportError:
        return _nodata("facility_economics", "packages/economics not importable")

    out = {"status": "ok"}
    if encounters_path and os.path.exists(encounters_path):
        enc = _read_jsonl(encounters_path)
        out["margins"] = E.margin_report(enc)  # worst-margin-first lines
        out["margin_worst"] = out["margins"]["lines"][0] if out["margins"]["lines"] else None
    else:
        out["margins"] = []
        out["margin_worst"] = None

    if enrollees_path and os.path.exists(enrollees_path):
        enrollees = _read_jsonl(enrollees_path)
        out["panel"] = E.panel_pnl(
            enrollees, _read_jsonl(encounters_path)
            if encounters_path and os.path.exists(encounters_path) else [],
            capitation_kobo)
    else:
        out["panel"] = None

    if claims_path and os.path.exists(claims_path):
        out["denials"] = E.denial_report(_read_jsonl(claims_path))
    else:
        out["denials"] = None
    return out


# ---------------------------------------------------------------- panel risk
def panel_risk(enrollees_path):
    """Enrolled panel by risk band — the capitation adequacy denominator."""
    if not enrollees_path or not os.path.exists(enrollees_path):
        return _nodata("panel_risk", "no enrollees export")
    import economics as E
    enrollees = _read_jsonl(enrollees_path)
    bands = {b: 0 for b in BAND_ORDER}
    for en in enrollees:
        band = E.risk_band(en)
        bands[band] = bands.get(band, 0) + 1
    n = max(1, len(enrollees))
    return {"status": "ok", "panel_size": len(enrollees),
            "bands": bands,
            "band_share_pct": {b: round(100 * c / n, 1) for b, c in bands.items()}}


# ---------------------------------------------------------------- catchments
def _haversine_km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def assign_catchments(members, facilities):
    """Nearest-facility assignment + per-facility load and travel stats.

    THE Sedona swap: this double loop becomes a KNN spatial join at state
    scale; inputs and outputs keep this exact shape.
    """
    geo = [f for f in facilities
           if f.get("latitude") is not None and f.get("longitude") is not None]
    if not geo:
        return _nodata("catchments", "no facilities with coordinates")
    per_fac = {f["id"]: {"assigned": 0, "distances_km": []} for f in geo}
    unassigned = 0
    for m in members:
        lat, lng = m.get("latitude"), m.get("longitude")
        if lat is None or lng is None:
            unassigned += 1
            continue
        best, best_d = None, None
        for f in geo:
            d = _haversine_km(lat, lng, f["latitude"], f["longitude"])
            if best_d is None or d < best_d:
                best, best_d = f, d
        per_fac[best["id"]]["assigned"] += 1
        per_fac[best["id"]]["distances_km"].append(best_d)

    rows = []
    for f in geo:
        rec = per_fac[f["id"]]
        ds = sorted(rec["distances_km"])
        rows.append({
            "facility": f["id"],
            "name": f.get("name", f["id"]),
            "assigned": rec["assigned"],
            "mean_km": round(sum(ds) / len(ds), 2) if ds else None,
            "p90_km": round(ds[int(0.9 * (len(ds) - 1))], 2) if ds else None,
            "over_15km": sum(1 for d in ds if d > 15),
        })
    rows.sort(key=lambda r: -r["assigned"])
    return {"status": "ok",
            "members": len(members), "unassigned": unassigned,
            "facilities": rows,
            "over_15km_total": sum(r["over_15km"] for r in rows)}


# ------------------------------------------------------- geo intelligence
# (ADR-0051): three upgrades that turn "nearest" into "right".

# Nigeria travel-time model: distance is not time. A straight-line km on
# a Lagos arterial and on a rural track are different journeys. Speed by
# road class (km/h); the ROAD_FACTOR from packages/routing converts
# haversine to road distance when no graph is available.
ROAD_FACTOR = 1.4
ROAD_SPEED_KMH = {"urban": 22.0, "periurban": 35.0, "rural": 28.0}
WALK_SPEED_KMH = 4.5  # catchment walking standard (WHO access studies)


def travel_minutes(dist_km: float, road_class: str = "rural",
                   walk_threshold_km: float = 2.0) -> float:
    """Door-to-facility minutes: walk under threshold, else motorised at
    the road-class speed over road-factored distance."""
    if dist_km <= walk_threshold_km:
        return dist_km / WALK_SPEED_KMH * 60
    road_km = dist_km * ROAD_FACTOR
    return road_km / ROAD_SPEED_KMH.get(road_class, ROAD_SPEED_KMH["rural"]) * 60


def assign_catchments_capacity(members, facilities, capacity_per_bed_day: float = 30.0):
    """Capacity-aware assignment: a member goes to the nearest facility
    THAT STILL HAS HEADROOM. Capacity proxy: beds x capacity_per_bed_day
    (monthly outpatient equivalents); a zero-capacity facility takes
    nobody. Overloaded facilities push overflow to their next-nearest —
    which is exactly the referral-steering question managers ask.

    Returns assign_catchments' shape plus overflow and saturation, so
    dashboards read one contract.
    """
    geo = [f for f in facilities
           if f.get("latitude") is not None and f.get("longitude") is not None]
    if not geo:
        return _nodata("catchments", "no facilities with coordinates")
    cap = {f["id"]: max(1, int(f.get("beds", 0) or 0)) * capacity_per_bed_day for f in geo}
    per_fac = {f["id"]: {"assigned": 0, "distances_km": []} for f in geo}
    unassigned = overflow = 0
    for m in members:
        lat, lng = m.get("latitude"), m.get("longitude")
        if lat is None or lng is None:
            unassigned += 1
            continue
        ranked = sorted(geo, key=lambda f: _haversine_km(lat, lng, f["latitude"], f["longitude"]))
        chosen, chose_d, spilled = None, None, False
        for i, f in enumerate(ranked):
            d = _haversine_km(lat, lng, f["latitude"], f["longitude"])
            if per_fac[f["id"]]["assigned"] < cap[f["id"]]:
                chosen, chose_d = f, d
                spilled = i > 0
                break
        if chosen is None:  # every facility saturated — nearest, flagged
            chosen, chose_d, spilled = ranked[0], _haversine_km(
                lat, lng, ranked[0]["latitude"], ranked[0]["longitude"]), True
        per_fac[chosen["id"]]["assigned"] += 1
        per_fac[chosen["id"]]["distances_km"].append(chose_d)
        if spilled:
            overflow += 1

    rows = []
    for f in geo:
        rec = per_fac[f["id"]]
        ds = sorted(rec["distances_km"])
        c = cap[f["id"]]
        rows.append({
            "facility": f["id"], "name": f.get("name", f["id"]),
            "assigned": rec["assigned"], "capacity": c,
            "saturation_pct": round(100 * rec["assigned"] / c, 1),
            "mean_km": round(sum(ds) / len(ds), 2) if ds else None,
            "p90_km": round(ds[int(0.9 * (len(ds) - 1))], 2) if ds else None,
            "over_15km": sum(1 for d in ds if d > 15),
            "mean_travel_min": round(sum(travel_minutes(d) for d in ds) / len(ds), 1) if ds else None,
        })
    rows.sort(key=lambda r: -r["assigned"])
    return {"status": "ok", "members": len(members), "unassigned": unassigned,
            "overflow": overflow, "facilities": rows,
            "over_15km_total": sum(r["over_15km"] for r in rows)}


def coverage_gaps(members, facilities, threshold_km: float = 15.0,
                  road_class: str = "rural", max_minutes: float = 120.0):
    """Who is unserved? Members whose nearest facility is beyond the
    distance OR travel-time line. This is the equity question: the
    catchment table says how loaded facilities are; THIS says who the
    network forgets entirely. Returns clusterable points so planners can
    site new PHCs where the gaps actually are."""
    geo = [f for f in facilities
           if f.get("latitude") is not None and f.get("longitude") is not None]
    if not geo:
        return _nodata("coverage_gaps", "no facilities with coordinates")
    gaps = []
    for m in members:
        lat, lng = m.get("latitude"), m.get("longitude")
        if lat is None or lng is None:
            continue
        d = min(_haversine_km(lat, lng, f["latitude"], f["longitude"]) for f in geo)
        mins = travel_minutes(d, road_class)
        if d > threshold_km or mins > max_minutes:
            gaps.append({"latitude": lat, "longitude": lng,
                         "nearest_km": round(d, 2),
                         "travel_min": round(mins, 1),
                         "state": m.get("state", ""), "lga": m.get("lga", "")})
    by_lga: dict[str, int] = {}
    for g in gaps:
        by_lga[g["lga"] or "unknown"] = by_lga.get(g["lga"] or "unknown", 0) + 1
    return {"status": "ok", "gap_members": len(gaps),
            "gap_share_pct": round(100 * len(gaps) / len(members), 2) if members else None,
            "threshold_km": threshold_km, "max_minutes": max_minutes,
            "by_lga": dict(sorted(by_lga.items(), key=lambda kv: -kv[1])),
            "points": gaps[:5000]}  # cap: planners cluster, they don't eyeball 1M dots


# ---------------------------------------------------------------- ml health
def _registry_models(registry_dir):
    """Champion versions + holdout metrics straight from the file registry —
    no import of openhealth_ml: the layout IS the interface."""
    out = []
    if not registry_dir or not os.path.isdir(registry_dir):
        return out
    for name in sorted(os.listdir(registry_dir)):
        mdir = os.path.join(registry_dir, name)
        champ_file = os.path.join(mdir, "champion")
        if not os.path.isfile(champ_file):
            continue
        with open(champ_file) as fh:
            champ = fh.read().strip()
        rec = {"model": name, "champion": champ}
        vdir = os.path.join(mdir, champ)
        for fn, key in (("metrics.json", "metrics"), ("card.json", "card")):
            try:
                rec[key] = _read_json(os.path.join(vdir, fn))
            except (OSError, json.JSONDecodeError):
                rec[key] = None
        # champion age: version dir mtime is the registry's only clock
        try:
            age_s = datetime.now(timezone.utc).timestamp() - os.path.getmtime(vdir)
            rec["champion_age_days"] = round(age_s / 86400, 1)
        except OSError:
            rec["champion_age_days"] = None
        out.append(rec)
    return out


def ml_health(registry_dir, reference_claims_path, current_claims_path):
    out = {"status": "ok", "models": _registry_models(registry_dir)}
    if not out["models"]:
        out["status"] = "no-data"
        out["reason"] = "registry empty or missing"
    if (reference_claims_path and current_claims_path
            and os.path.exists(reference_claims_path)
            and os.path.exists(current_claims_path)):
        try:
            from openhealth_ml import monitor as M
            out["drift"] = M.drift_report(
                _read_jsonl(reference_claims_path), _read_jsonl(current_claims_path))
        except ImportError:
            out["drift"] = {"verdict": "unavailable",
                            "detail": "ml/openhealth_ml not importable"}
    else:
        out["drift"] = {"verdict": "no-data",
                        "detail": "no reference/current claims windows"}
    return out


# ---------------------------------------------------------------- assembly
def build_plane(lakehouse_dir=None, facilities_path=None, members_path=None,
                enrollees_path=None, encounters_path=None, claims_path=None,
                claims_reference_path=None, registry_dir=None,
                capitation_kobo=1450_00):
    plane = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sections": {
            "facility_economics": facility_economics(
                encounters_path, enrollees_path, claims_path, capitation_kobo),
            "panel_risk": panel_risk(enrollees_path),
            "catchments": (
                assign_catchments_capacity(_read_jsonl(members_path), _read_json(facilities_path))
                if members_path and facilities_path
                and os.path.exists(members_path) and os.path.exists(facilities_path)
                else _nodata("catchments", "no members/facilities export")),
            "coverage_gaps": (
                coverage_gaps(_read_jsonl(members_path), _read_json(facilities_path))
                if members_path and facilities_path
                and os.path.exists(members_path) and os.path.exists(facilities_path)
                else _nodata("coverage_gaps", "no members/facilities export")),
            "ml_health": ml_health(registry_dir, claims_reference_path, claims_path),
        },
    }
    # lakehouse aggregates are echoed through so the manager sees event
    # coverage next to economics without a second fetch.
    if lakehouse_dir:
        agg = os.path.join(lakehouse_dir, "gold", "aggregates.json")
        if os.path.exists(agg):
            plane["lakehouse_aggregates"] = _read_json(agg)
    return plane


def main(argv=None):
    ap = argparse.ArgumentParser(description="nightly KPI plane build (ADR-0033)")
    ap.add_argument("--lakehouse", default="lakehouse")
    ap.add_argument("--facilities")
    ap.add_argument("--members")
    ap.add_argument("--enrollees")
    ap.add_argument("--encounters")
    ap.add_argument("--claims")
    ap.add_argument("--claims-reference")
    ap.add_argument("--registry", default=os.path.join(_REPO, "ml", "registry"))
    ap.add_argument("--capitation-kobo", type=int, default=1450_00)
    ap.add_argument("--out", help="default <lakehouse>/gold/kpi_plane.json")
    args = ap.parse_args(argv)

    plane = build_plane(
        lakehouse_dir=args.lakehouse,
        facilities_path=args.facilities,
        members_path=args.members,
        enrollees_path=args.enrollees,
        encounters_path=args.encounters,
        claims_path=args.claims,
        claims_reference_path=args.claims_reference,
        registry_dir=args.registry,
        capitation_kobo=args.capitation_kobo)
    out = args.out or os.path.join(args.lakehouse, "gold", "kpi_plane.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(plane, fh, indent=2, sort_keys=True)
    ok = [k for k, v in plane["sections"].items() if v.get("status") == "ok"]
    print(json.dumps({"out": out, "sections_ok": ok}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
