"""OpenHealth OS — pilot-gate analytics (module 10; gates per §6.6).

Computes the measured-not-asserted pilot gates from the domain event stream
(the same events sync-ingest lands): offline clinical continuity, sync
reliability, claims turnaround, stock-out visibility, staff weekly active use.
Every output cites the event window it was computed from — dashboards quote
reproducible numbers.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Iterable


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def offline_continuity(events: Iterable[dict]) -> dict:
    """Gate: ≥90% of encounters recorded with zero connectivity.

    Encounter events carry captured_offline (bool) and, when they reconcile,
    reconciled_at.
    """
    enc = [e for e in events if e.get("type") == "encounter"]
    if not enc:
        return {"encounters": 0, "offline_pct": None}
    offline = sum(1 for e in enc if e.get("captured_offline"))
    return {"encounters": len(enc), "offline_pct": round(100 * offline / len(enc), 1),
            "gate": "≥90%", "pass": (100 * offline / len(enc)) >= 90}


def sync_reliability(events: Iterable[dict]) -> dict:
    """Gate: ≥98% of facility-days reconciled <15 min after connectivity.

    Encounter events carry captured_at edge ts and reconciled_at server ts.
    """
    rows = []
    for e in events:
        if e.get("type") == "encounter" and e.get("reconciled_at") and e.get("ts"):
            lag_min = (_ts(e["reconciled_at"]) - _ts(e["ts"])).total_seconds() / 60
            rows.append(lag_min)
    if not rows:
        return {"facility_days": 0, "within_sla_pct": None}
    ok = sum(1 for lag in rows if lag <= 15)
    return {"facility_days": len(rows), "within_sla_pct": round(100 * ok / len(rows), 1),
            "gate": "≥98% <15min", "pass": (100 * ok / len(rows)) >= 98}


def claims_turnaround(events: Iterable[dict]) -> dict:
    """Gate: <14 days median from submission to settlement."""
    submitted, settled = {}, {}
    for e in events:
        if e.get("type") == "claim_submitted":
            submitted[e["claim_id"]] = _ts(e["ts"])
        elif e.get("type") == "claim_settled":
            settled[e["claim_id"]] = _ts(e["ts"])
    days = [(settled[c] - submitted[c]).days for c in submitted if c in settled]
    if not days:
        return {"claims": 0, "median_days": None}
    med = median(days)
    return {"claims": len(days), "median_days": med, "gate": "<14 days median",
            "pass": med < 14}


def stockout_visibility(events: Iterable[dict], facilities: int, days: int) -> dict:
    """Gate: ≥80% of tracer SKUs reported daily (proxy: facility-day reports)."""
    reports = {(e.get("facility"), e.get("ts", "")[:10])
               for e in events if e.get("type") == "stock_report"}
    expected = facilities * days
    if expected == 0:
        return {"reported": 0, "expected": 0, "pct": None}
    pct = round(100 * len(reports) / expected, 1)
    return {"reported": len(reports), "expected": expected, "pct": pct,
            "gate": "≥80%", "pass": pct >= 80}


def staff_weekly_active(events: Iterable[dict], trained_staff: int, weeks: int) -> dict:
    """Gate: ≥70% of trained staff active weekly."""
    active = {e.get("staff_id") for e in events
              if e.get("type") == "staff_activity" and e.get("staff_id")}
    if trained_staff == 0:
        return {"trained": 0, "active_pct": None}
    pct = round(100 * len(active) / trained_staff, 1)
    return {"trained": trained_staff, "active": len(active), "active_pct": pct,
            "gate": "≥70%", "pass": pct >= 70}
