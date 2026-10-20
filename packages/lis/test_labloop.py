import json, subprocess, sys, os
from labloop import Result, evaluate, batch

def test_autoverify_normal():
    assert evaluate(Result("K", 4.2, "P1"))["disposition"] == "autoverified"

def test_critical_low_alerts():
    e = evaluate(Result("K", 2.1, "P1"))
    assert e["disposition"] == "critical" and "CRITICAL" in e["alert"]

def test_critical_high():
    assert evaluate(Result("GLU", 30, "P1"))["disposition"] == "critical"

def test_delta_check_review():
    e = evaluate(Result("HB", 15.0, "P1", prior=10.0))  # +50% > 25% limit
    assert e["disposition"] == "review" and "delta" in e["reason"]

def test_delta_ok():
    assert evaluate(Result("HB", 12.5, "P1", prior=12.0))["disposition"] == "autoverified"

def test_reflex_suggestion():
    e = evaluate(Result("GLU", 9.0, "P1"))
    assert e["disposition"] == "flagged" and e["reflex"] == "HbA1c"

def test_unknown_analyte_honest():
    assert evaluate(Result("ZZZ", 1, "P1"))["disposition"] == "review"

def test_batch_exit_loud_on_critical():
    p = subprocess.run([sys.executable, "labloop.py"],
        input=json.dumps({"results": [{"analyte": "K", "value": 2.0, "patient_id": "P1"}]}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert p.returncode == 1 and "CRITICAL" in p.stdout
