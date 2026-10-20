import json, subprocess, sys, os
from nhia import build_submission

TARIFFS = {"CONS-GEN", "MAT-NVD", "MAL-RDT"}

def ffs(member="NG-1", **kw):
    return {"member_id": member, "encounter_id": "E1", "tariff_code": "CONS-GEN",
            "amount": 1500, "date": "2026-10-01", **kw}

def test_valid_ffs():
    out = build_submission("NHIA-FFS", "F1", "2026-10", [ffs()], TARIFFS.__contains__)
    assert out["conformant"] and out["submitted"] == 1
    assert out["submission"]["total_amount"] == 1500
    assert len(out["submission"]["submission_hash"]) == 16

def test_bad_tariff_rejected_but_submission_continues():
    out = build_submission("NHIA-FFS", "F1", "2026-10",
        [ffs("A"), ffs("B", tariff_code="FAKE")], TARIFFS.__contains__)
    assert not out["conformant"] and out["submitted"] == 1
    assert out["rejected_claims"][0]["claim"] == "B"

def test_capitation():
    out = build_submission("NHIA-CAP", "F1", "2026-10",
        [{"member_id": "A", "period": "2026-10", "capitation_rate": 750}])
    assert out["conformant"] and out["submission"]["total_amount"] == 750

def test_unknown_scheme():
    out = build_submission("BOGUS", "F1", "2026-10", [])
    assert not out["conformant"]

def test_missing_fields():
    out = build_submission("NHIA-FFS", "F1", "2026-10", [{"member_id": "A"}])
    assert not out["conformant"] and out["submitted"] == 0

def test_negative_amount():
    out = build_submission("NHIA-FFS", "F1", "2026-10", [ffs(amount=-5)])
    assert not out["conformant"]

def test_cli_exit_code():
    p = subprocess.run([sys.executable, "nhia.py"],
        input=json.dumps({"scheme": "NHIA-FFS", "facility_id": "F1", "period": "2026-10",
                          "claims": [{"member_id": "A"}]}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert p.returncode == 1
