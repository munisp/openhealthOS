import json, subprocess, sys, os
import pytest
from orders import (OrderService, OrderError, TransitionError, OverrideRequired, ORDER_SETS)
from formulary import Formulary, AllergyConflict

def svc():
    return OrderService(credential_check=lambda pid: pid == "DR-1")

def test_allergy_fail_closed():
    fx = Formulary()
    with pytest.raises(AllergyConflict):
        fx.check_allergy("NG-AMOX500", ["penicillin"])

def test_interaction_warn():
    fx = Formulary()
    f = fx.check_interactions(["NG-LISIN10", "NG-IBU400"])
    assert f and f[0]["severity"] == "major"

def test_dose_max():
    fx = Formulary()
    f = fx.check_dose("NG-PARA500", 1000, 6)  # 6g/day
    assert f and "4g" in f[0]["message"] or "4000" in f[0]["message"]

def test_order_lifecycle():
    s = svc()
    o = s.draft("O1", "P1", "DR-1", "NG-AMOX500", 500, 3, 7)
    s.screen("O1", [], [])
    s.place("O1")
    o.advance("verified"); o.advance("dispensed"); o.advance("completed")
    assert o.status == "completed"
    with pytest.raises(TransitionError):
        o.advance("placed")

def test_override_required_for_major():
    s = svc()
    s.draft("O1", "P1", "DR-1", "NG-IBU400", 400, 3, 5)
    s.screen("O1", [], ["NG-LISIN10"])
    with pytest.raises(OverrideRequired):
        s.place("O1")
    s.place("O1", override_reasons=[{"finding": "nsaid+acei", "reason": "short course, renal function normal"}])
    assert s.orders["O1"].overrides

def test_uncredentialed_prescriber_blocked():
    s = svc()
    s.draft("O1", "P1", "DR-X", "NG-AMOX500", 500, 3, 7)
    s.screen("O1", [], [])
    with pytest.raises(OrderError):
        s.place("O1")

def test_order_sets():
    s = svc()
    orders = s.draft_from_set("malaria-uncomplicated", "OS1", "P1", "DR-1")
    assert len(orders) == len(ORDER_SETS["malaria-uncomplicated"])
    with pytest.raises(OrderError):
        s.draft_from_set("nope", "OS2", "P1", "DR-1")

def test_unknown_drug_rejected():
    s = svc()
    with pytest.raises(OrderError):
        s.draft("O1", "P1", "DR-1", "FAKE-1", 1, 1, 1)

def test_cli_screen_blocks_allergy():
    p = subprocess.run([sys.executable, "orders.py", "screen"],
        input=json.dumps({"code": "NG-AMOX500", "allergies": ["penicillin"], "active": []}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    out = json.loads(p.stdout)
    assert out["ok"] is False and "ALLERGY" in out["block"]

def test_cli_screen_ok():
    p = subprocess.run([sys.executable, "orders.py", "screen"],
        input=json.dumps({"code": "NG-PARA500", "allergies": [], "active": ["NG-ACT-AL"],
                          "dose_mg": 500, "doses_per_day": 3}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    out = json.loads(p.stdout)
    assert out["ok"] is True

def test_extra_formulary_layer(tmp_path):
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps({"drugs": [{"code": "X-1", "name": "TestDrug", "class": "nsaid", "form": "t", "strength": "1mg", "eml": False}]}))
    fx = Formulary(str(extra))
    f = fx.check_interactions(["X-1", "NG-LISIN10"])
    assert f  # class-level rule applies to deployer-added drug
