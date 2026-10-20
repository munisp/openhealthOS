import json, subprocess, sys, os, pytest
from steward import aware_report, iv_oral_switches, adr_report

RX = [{"code": "NG-AMOX500", "days": 5, "route": "oral"},
      {"code": "NG-CIPRO500", "days": 7, "route": "oral"}]

def test_aware_report():
    r = aware_report(RX)
    assert r["counts"]["Access"] == 1 and r["counts"]["Watch"] == 1
    assert r["access_pct"] == 50.0 and not r["who_target_met"]
    assert r["watch_items"] == ["NG-CIPRO500"]

def test_who_target():
    r = aware_report([{"code": "NG-AMOX500"}, {"code": "NG-DOXY100"}, {"code": "NG-CIPRO500"}])
    assert r["who_target_met"]

def test_iv_oral_switch():
    out = iv_oral_switches([{"code": "NG-AMOX500", "days": 3, "route": "iv"}])
    assert out and out[0]["suggest_oral"] == "NG-AMOX250S"
    assert not iv_oral_switches([{"code": "NG-AMOX500", "days": 1, "route": "iv"}])

def test_adr_report():
    r = adr_report("P1", "NG-AMOX500", "rash", "moderate", "2026-10-01", "DR-1")
    assert r["format"] == "nafdac-e-reporting/v1"
    with pytest.raises(ValueError):
        adr_report("P1", "X", "y", "weird", "2026-01-01", "DR-1")

def test_cli():
    p = subprocess.run([sys.executable, "steward.py"], input=json.dumps({"prescriptions": RX}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["counts"]["Watch"] == 1
