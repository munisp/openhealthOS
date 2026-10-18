#!/usr/bin/env python3
"""LGA/state rollups — batch analytics over the facility registry and the
domain event stream (ADR-0015; closes the "no batch geospatial analytics"
gap in the Geospatial row and gives the Analytics row its national view).

The interactive path (haversine near-me, the offline map) answers "where
is care near this patient?". The batch path here answers the planning
questions a state ministry actually asks:

  * how many facilities per LGA, by level and ownership?
  * where are the beds, doctors, nurses — and where are they missing?
  * which LGAs lack cold-chain / oxygen / maternity coverage?
  * what do claims volumes and stock-outs look like per LGA?

Pure stdlib, pure functions: registry rows in, rollups out. The state
dashboard renders the JSON; render_markdown produces the printable
ministry brief. Every number is reproducible from its inputs — the same
measured-not-asserted doctrine as the pilot gates.
"""
from __future__ import annotations

from typing import Iterable

AMENITIES = ["pharmacy", "lab", "maternity", "xray", "ambulance", "oxygen", "cold-chain"]


def _blank_lga() -> dict:
    return {
        "facilities": 0,
        "by_level": {},          # PHC | clinic | hospital
        "by_ownership": {},      # public | private | faith-based
        "bhcpf": 0,
        "beds": 0, "doctors": 0, "nurses": 0,
        "amenity_coverage": {a: 0 for a in AMENITIES},  # facilities offering each
        "power": {}, "water": {},
        "geocoded": 0,           # facilities with coordinates
        # event-derived
        "claims_submitted": 0, "claims_approved": 0, "claims_rejected": 0,
        "claims_approved_ngn": 0.0,
        "stockouts": 0,
    }


def facility_rollup(facilities: Iterable[dict], events: Iterable[dict] = ()) -> dict:
    """Roll facilities + events up per (state, LGA).

    facilities: registry rows (the /facilities wire shape).
    events: domain events — claim events carry
        {"type":"claim","facility_id":...,"status":"submitted|approved|rejected",
         "amount_ngn":float}
    and stock-out events {"type":"stockout","facility_id":...}.
    """
    by_id = {f.get("id"): f for f in facilities}
    lgas: dict[tuple[str, str], dict] = {}

    def bucket(state: str, lga: str) -> dict:
        return lgas.setdefault((state or "?", lga or "?"), _blank_lga())

    for f in facilities:
        b = bucket(f.get("state", ""), f.get("lga", ""))
        b["facilities"] += 1
        b["by_level"][f.get("level", "?")] = b["by_level"].get(f.get("level", "?"), 0) + 1
        own = f.get("ownership", "?")
        b["by_ownership"][own] = b["by_ownership"].get(own, 0) + 1
        if f.get("bhcpf"):
            b["bhcpf"] += 1
        b["beds"] += int(f.get("beds") or 0)
        b["doctors"] += int(f.get("doctors") or 0)
        b["nurses"] += int(f.get("nurses") or 0)
        for a in f.get("amenities") or []:
            if a in b["amenity_coverage"]:
                b["amenity_coverage"][a] += 1
        pw = f.get("power", "?")
        b["power"][pw] = b["power"].get(pw, 0) + 1
        wt = f.get("water", "?")
        b["water"][wt] = b["water"].get(wt, 0) + 1
        if f.get("latitude") and f.get("longitude"):
            b["geocoded"] += 1

    for e in events:
        fac = by_id.get(e.get("facility_id"))
        if fac is None:
            continue  # event for an unknown facility can't be attributed
        b = bucket(fac.get("state", ""), fac.get("lga", ""))
        if e.get("type") == "claim":
            status = e.get("status", "submitted")
            key = f"claims_{status}"
            if key in b and status in ("submitted", "approved", "rejected"):
                b[key] += 1
            if status == "approved":
                b["claims_approved_ngn"] += float(e.get("amount_ngn") or 0)
        elif e.get("type") == "stockout":
            b["stockouts"] += 1

    out = {}
    for (state, lga), b in sorted(lgas.items()):
        n = b["facilities"] or 1
        b["amenity_coverage_pct"] = {a: round(100 * c / n, 1)
                                     for a, c in b["amenity_coverage"].items()}
        b["beds_per_facility"] = round(b["beds"] / n, 1)
        b["staff_per_facility"] = round((b["doctors"] + b["nurses"]) / n, 1)
        out.setdefault(state, {})[lga] = b
    return out


def state_rollup(lga_rollup: dict) -> dict:
    """Aggregate the per-LGA rollups to one row per state."""
    out = {}
    for state, lgas in lga_rollup.items():
        s = _blank_lga()
        for b in lgas.values():
            s["facilities"] += b["facilities"]
            for k, v in b["by_level"].items():
                s["by_level"][k] = s["by_level"].get(k, 0) + v
            for k, v in b["by_ownership"].items():
                s["by_ownership"][k] = s["by_ownership"].get(k, 0) + v
            s["bhcpf"] += b["bhcpf"]
            s["beds"] += b["beds"]
            s["doctors"] += b["doctors"]
            s["nurses"] += b["nurses"]
            for a in AMENITIES:
                s["amenity_coverage"][a] += b["amenity_coverage"][a]
            s["geocoded"] += b["geocoded"]
            s["claims_submitted"] += b["claims_submitted"]
            s["claims_approved"] += b["claims_approved"]
            s["claims_rejected"] += b["claims_rejected"]
            s["claims_approved_ngn"] += b["claims_approved_ngn"]
            s["stockouts"] += b["stockouts"]
        n = s["facilities"] or 1
        s["amenity_coverage_pct"] = {a: round(100 * c / n, 1)
                                     for a, c in s["amenity_coverage"].items()}
        s["beds_per_facility"] = round(s["beds"] / n, 1)
        s["staff_per_facility"] = round((s["doctors"] + s["nurses"]) / n, 1)
        s["lgas"] = len(lgas)
        out[state] = s
    return out


def render_markdown(lga_rollup: dict, state_rows: dict) -> str:
    """The ministry brief: one table per state, LGA per row."""
    lines = ["# State facility & activity rollups", ""]
    for state in sorted(lga_rollup):
        s = state_rows.get(state, {})
        lines.append(f"## {state} — {s.get('facilities', 0)} facilities across "
                     f"{s.get('lgas', 0)} LGAs; {s.get('beds', 0)} beds, "
                     f"{s.get('doctors', 0)} doctors, {s.get('nurses', 0)} nurses")
        lines.append("")
        lines.append("| LGA | Facilities | PHC | Hospital | Beds | Doctors | Nurses | "
                     "Oxygen % | Cold-chain % | Claims approved (NGN) | Stock-outs |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for lga, b in sorted(lga_rollup[state].items()):
            lines.append(
                f"| {lga} | {b['facilities']} | {b['by_level'].get('PHC', 0)} "
                f"| {b['by_level'].get('hospital', 0)} | {b['beds']} | {b['doctors']} "
                f"| {b['nurses']} | {b['amenity_coverage_pct']['oxygen']} "
                f"| {b['amenity_coverage_pct']['cold-chain']} "
                f"| {b['claims_approved_ngn']:,.0f} | {b['stockouts']} |")
        lines.append("")
    return "\n".join(lines)
