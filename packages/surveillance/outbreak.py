"""Outbreak early-warning (ADR-0058): anomaly detection on syndromic feeds
(CUSUM-lite over rolling baseline) plus geospatial clustering flags. Doctrine:
an alert is a SIGNAL for investigation, never a declared outbreak; k-anonymity
floor from notifiable.py still applies to anything published.
"""
from __future__ import annotations
import json, math, statistics, sys

def detect_anomalies(series: list[int], lookback: int = 14, threshold_sigma: float = 3.0,
                     min_count: int = 5) -> list[dict]:
    """series: daily case counts (oldest first). Flags days exceeding the
    rolling baseline mean + threshold*sigma. min_count avoids noise on tiny
    denominators."""
    alerts = []
    for i in range(lookback, len(series)):
        base = series[i - lookback:i]
        mu = statistics.fmean(base)
        sigma = statistics.pstdev(base) or 0.5  # floor: zero-variance baselines still alert
        today = series[i]
        if today >= min_count and today > mu + threshold_sigma * sigma:
            alerts.append({"day_index": i, "count": today, "baseline": round(mu, 1),
                           "excess": round(today - mu, 1),
                           "signal": "investigate — possible outbreak, NOT confirmed"})
    return alerts

def cluster(cases: list[dict], radius_km: float = 5.0, min_cases: int = 3) -> list[dict]:
    """Naive geographic clustering: groups cases within radius_km of a seed.
    cases: [{lat, lng, ...}]"""
    used = [False] * len(cases)
    clusters = []
    for i, c in enumerate(cases):
        if used[i]:
            continue
        group = [c]; used[i] = True
        for j in range(i + 1, len(cases)):
            if used[j]:
                continue
            d = _km(c, cases[j])
            if d <= radius_km:
                group.append(cases[j]); used[j] = True
        if len(group) >= min_cases:
            clusters.append({"size": len(group),
                             "centroid": {"lat": round(sum(g["lat"] for g in group) / len(group), 4),
                                          "lng": round(sum(g["lng"] for g in group) / len(group), 4)},
                             "signal": "geographic cluster — dispatch investigation"})
    return clusters

def _km(a: dict, b: dict) -> float:
    R = 6371.0
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp = math.radians(b["lat"] - a["lat"]); dl = math.radians(b["lng"] - a["lng"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))

def main(argv):
    doc = json.load(sys.stdin)
    out = {"anomalies": detect_anomalies(doc.get("series", []))}
    if "cases" in doc:
        out["clusters"] = cluster(doc["cases"])
    print(json.dumps(out, indent=2))
    return 0 if not out["anomalies"] and not out.get("clusters") else 1  # fail-LOUD

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
