"""Consumption-based supply forecasting (ADR-0058): moving-average demand,
safety stock, reorder points, stockout-risk ranking. Stdlib statistics only —
no JVM, no heavy deps. Feeds procurement drafts (never auto-orders).
"""
from __future__ import annotations
import json, statistics, sys

def moving_average(series: list[float], window: int = 3) -> float:
    if not series:
        return 0.0
    w = series[-window:]
    return sum(w) / len(w)

def safety_stock(series: list[float], lead_time_days: int, service_z: float = 1.65) -> float:
    """z * sigma_daily * sqrt(lead_time). 95% service level default."""
    if len(series) < 2:
        return 0.0
    return service_z * statistics.pstdev(series) * (lead_time_days ** 0.5)

def plan(item: dict) -> dict:
    """item: {code, monthly_usage: [...], on_hand, lead_time_days}"""
    usage = item["monthly_usage"]
    daily = [u / 30 for u in usage]
    demand_daily = moving_average(daily)
    lt = item.get("lead_time_days", 14)
    ss = safety_stock(daily, lt)
    reorder_point = demand_daily * lt + ss
    days_of_stock = item.get("on_hand", 0) / demand_daily if demand_daily else float("inf")
    return {"code": item["code"], "daily_demand": round(demand_daily, 2),
            "safety_stock": round(ss, 1), "reorder_point": round(reorder_point, 1),
            "on_hand": item.get("on_hand", 0), "days_of_stock": round(days_of_stock, 1),
            "order_now": item.get("on_hand", 0) <= reorder_point,
            "suggested_qty": round(max(demand_daily * 30 - item.get("on_hand", 0) + ss, 0), 0)}

def rank_stockout_risk(items: list[dict]) -> list[dict]:
    return sorted((plan(i) for i in items), key=lambda p: p["days_of_stock"])

def main(argv):
    doc = json.load(sys.stdin)
    out = [plan(i) for i in doc.get("items", [])]
    print(json.dumps({"plan": rank_stockout_risk(doc.get("items", [])), "count": len(out)}, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
