"""Catchment analytics (ADR-0015) — the geospatial batch job.

/facilities/near answers the point question ("what is around me, right
now"). Planning needs the aggregate question: which LGAs are underserved,
where are beds per capita below floor, how far is the average member from
their assigned facility. That is a batch job over the lakehouse, not an
API — and it is deliberately stdlib Python (the "Sedona row" answer: Spark
is the wrong tier for one country's nightly planning job).

Inputs
------
facilities : CSV in the facility-import format (data/facilities/*.csv)
members    : JSONL {"member_id", "lga", "latitude", "longitude"} — a
             lakehouse silver export in production; --members-n generates
             a deterministic synthetic panel for development.

Output: per-LGA and national rollups + the underserved list, written to
lakehouse/gold/catchment_report.json by default.

    PYTHONPATH=ml python3 -m openhealth_ml.catchment \
        --facilities data/facilities/ng-phc-sample.csv --members-n 20000
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict

EARTH_RADIUS_KM = 6371.0088  # IUGG mean — matches the registry endpoint

# Planning floors (named, tunable — not hidden in the math):
BEDS_PER_1K_FLOOR = 1.0        # beds per 1,000 catchment members
MEMBERS_PER_FACILITY_CEIL = 10_000
AVG_DISTANCE_KM_CEIL = 5.0     # WHO: care within ~5km / 1h walk for PHC


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def load_facilities_csv(path: str) -> list[dict]:
    """Read the facility-import CSV format. Ungelocated rows are kept (they
    count toward capacity) but never win an assignment."""
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            def num(key, cast):
                v = (row.get(key) or "").strip()
                return cast(v) if v else None
            out.append({
                "id": (row.get("id") or "").strip(),
                "name": (row.get("name") or "").strip(),
                "state": (row.get("state") or "").strip(),
                "lga": (row.get("lga") or "").strip(),
                "latitude": num("latitude", float),
                "longitude": num("longitude", float),
                "beds": num("beds", int) or 0,
                "doctors": num("doctors", int) or 0,
                "nurses": num("nurses", int) or 0,
            })
    return out


def load_members_jsonl(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def generate_members(n: int, facilities: list[dict], seed: int = 7) -> list[dict]:
    """Synthetic panel: members cluster around real facility catchments
    (urban gravity) plus a sparse far-flung rural tail so the distance
    metric has something honest to measure."""
    rng = random.Random(seed)
    hubs = [f for f in facilities
            if f["latitude"] is not None and f["beds"] >= 10]
    if not hubs:
        return []
    members = []
    for i in range(n):
        if rng.random() < 0.85:
            h = rng.choice(hubs)
            lat = h["latitude"] + rng.gauss(0, 0.06)   # ~7km sigma urban
            lng = h["longitude"] + rng.gauss(0, 0.06)
            lga = h["lga"]
        else:
            lat = rng.uniform(4.5, 13.5)               # rural scatter
            lng = rng.uniform(3.0, 14.5)
            lga = rng.choice(hubs)["lga"] if rng.random() < 0.5 else "UNMAPPED"
        members.append({"member_id": f"MBR-{i:06d}", "lga": lga,
                        "latitude": lat, "longitude": lng})
    return members


def assign_nearest(member: dict, facilities: list[dict]) -> tuple[dict | None, float | None]:
    best, best_d = None, None
    for f in facilities:
        if f["latitude"] is None:
            continue
        d = haversine_km(member["latitude"], member["longitude"],
                         f["latitude"], f["longitude"])
        if best_d is None or d < best_d:
            best, best_d = f, d
    return best, best_d


def analyze(facilities: list[dict], members: list[dict]) -> dict:
    by_lga_facs: dict[str, list[dict]] = defaultdict(list)
    for f in facilities:
        by_lga_facs[f["lga"]].append(f)

    distances: dict[str, list[float]] = defaultdict(list)
    by_lga_members: dict[str, int] = defaultdict(int)
    assigned_fac: dict[str, int] = defaultdict(int)
    unassigned = 0
    for m in members:
        by_lga_members[m["lga"]] += 1
        f, d = assign_nearest(m, facilities)
        if f is None:
            unassigned += 1
            continue
        distances[m["lga"]].append(d)
        assigned_fac[f["id"]] += 1

    rows = []
    lgas = sorted(set(by_lga_facs) | set(by_lga_members))
    for lga in lgas:
        facs = by_lga_facs.get(lga, [])
        pop = by_lga_members.get(lga, 0)
        beds = sum(f["beds"] for f in facs)
        dists = distances.get(lga, [])
        avg_d = sum(dists) / len(dists) if dists else None
        beds_per_1k = (beds / pop * 1000) if pop else None
        per_fac = (pop / len(facs)) if facs else None
        reasons = []
        if beds_per_1k is not None and beds_per_1k < BEDS_PER_1K_FLOOR:
            reasons.append(f"beds_per_1k {beds_per_1k:.2f} < {BEDS_PER_1K_FLOOR}")
        if pop and not facs:
            reasons.append("members with no facility in LGA")
        if per_fac is not None and per_fac > MEMBERS_PER_FACILITY_CEIL:
            reasons.append(f"members_per_facility {per_fac:.0f} > {MEMBERS_PER_FACILITY_CEIL}")
        if avg_d is not None and avg_d > AVG_DISTANCE_KM_CEIL:
            reasons.append(f"avg_distance_km {avg_d:.1f} > {AVG_DISTANCE_KM_CEIL}")
        rows.append({
            "lga": lga,
            "state": facs[0]["state"] if facs else "",
            "facilities": len(facs),
            "geolocated_facilities": sum(1 for f in facs if f["latitude"] is not None),
            "beds": beds,
            "doctors": sum(f["doctors"] for f in facs),
            "nurses": sum(f["nurses"] for f in facs),
            "members": pop,
            "beds_per_1k_members": round(beds_per_1k, 3) if beds_per_1k is not None else None,
            "members_per_facility": round(per_fac, 1) if per_fac is not None else None,
            "avg_distance_km": round(avg_d, 2) if avg_d is not None else None,
            "underserved": bool(reasons),
            "reasons": reasons,
        })
    rows.sort(key=lambda r: (not r["underserved"], -(r["members"] or 0)))

    national = {
        "lgas": len(rows),
        "underserved_lgas": sum(1 for r in rows if r["underserved"]),
        "facilities": len(facilities),
        "members": sum(by_lga_members.values()),
        "members_unassigned": unassigned,
        "total_beds": sum(f["beds"] for f in facilities),
        "total_doctors": sum(f["doctors"] for f in facilities),
        "total_nurses": sum(f["nurses"] for f in facilities),
    }
    pop = national["members"]
    if pop:
        national["beds_per_1k_members"] = round(national["total_beds"] / pop * 1000, 3)
    return {"per_lga": rows, "national": national,
            "floors": {"beds_per_1k": BEDS_PER_1K_FLOOR,
                       "members_per_facility": MEMBERS_PER_FACILITY_CEIL,
                       "avg_distance_km": AVG_DISTANCE_KM_CEIL}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facilities", default="data/facilities/ng-phc-sample.csv")
    ap.add_argument("--members", default=None, help="lakehouse silver JSONL export")
    ap.add_argument("--members-n", type=int, default=20000,
                    help="synthetic panel size when --members absent")
    ap.add_argument("--out", default=None,
                    help="write report here (e.g. lakehouse/gold/catchment_report.json)")
    args = ap.parse_args()
    facilities = load_facilities_csv(args.facilities)
    if not facilities:
        print(json.dumps({"error": f"no facilities in {args.facilities}"}))
        return 1
    source = "lakehouse-silver"
    if args.members:
        members = load_members_jsonl(args.members)
    else:
        members = generate_members(args.members_n, facilities)
        source = "synthetic-panel"
    report = analyze(facilities, members)
    report["inputs"] = {"facilities": args.facilities, "members_source": source,
                        "members_n": len(members)}
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
