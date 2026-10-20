import json, subprocess, sys, os
from forecast import plan, rank_stockout_risk, moving_average

ITEM = {"code": "NG-AMOX500", "monthly_usage": [300, 330, 360], "on_hand": 50, "lead_time_days": 14}

def test_moving_average():
    assert moving_average([3, 6, 9]) == 6.0
    assert moving_average([]) == 0.0

def test_reorder_triggered():
    p = plan(ITEM)  # daily ~11, ROP ~155+ss; on_hand 50 -> order
    assert p["order_now"] and p["suggested_qty"] > 0

def test_no_reorder_when_stocked():
    p = plan({**ITEM, "on_hand": 5000})
    assert not p["order_now"] and p["suggested_qty"] == 0

def test_volatility_raises_safety_stock():
    calm = plan({**ITEM, "monthly_usage": [330, 330, 330]})
    wild = plan({**ITEM, "monthly_usage": [100, 600, 290]})
    assert wild["safety_stock"] > calm["safety_stock"]

def test_zero_usage_no_divide_by_zero():
    p = plan({"code": "X", "monthly_usage": [0, 0], "on_hand": 5})
    assert p["days_of_stock"] == float("inf") or p["daily_demand"] == 0

def test_risk_ranking():
    items = [ITEM, {"code": "B", "monthly_usage": [30, 30, 30], "on_hand": 900}]
    ranked = rank_stockout_risk(items)
    assert ranked[0]["code"] == "NG-AMOX500"

def test_cli():
    p = subprocess.run([sys.executable, "forecast.py"], input=json.dumps({"items": [ITEM]}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["count"] == 1
