import json, subprocess, sys, os
from accredit import score_inspection, renewal_due, CHECKLIST

FULL = {i["id"]: 95 for i in CHECKLIST}

def test_perfect_score_grade_a():
    r = score_inspection(FULL)
    assert r["grade"] == "A" and r["licence_action"] == "renew" and r["complete"]

def test_low_score_suspends():
    r = score_inspection({i["id"]: 20 for i in CHECKLIST})
    assert r["grade"] == "D" and r["licence_action"] == "suspend"

def test_conditional_band():
    r = score_inspection({i["id"]: 65 for i in CHECKLIST})
    assert r["grade"] == "C" and r["licence_action"] == "conditional"

def test_missing_sections_flagged():
    r = score_inspection({"staffing": 90})
    assert not r["complete"] and len(r["missing_sections"]) == len(CHECKLIST) - 1

def test_renewal_due_sorted():
    out = renewal_due([{"facility": "A", "expires": "2026-11-01"},
                       {"facility": "B", "expires": "2026-09-01"}], "2026-10-20")
    assert out[0]["facility"] == "B" and out[0]["expired"]
    assert out[1]["days_left"] == 12

def test_cli():
    p = subprocess.run([sys.executable, "accredit.py"], input=json.dumps({"scores": FULL}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["grade"] == "A"
