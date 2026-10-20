import json, os, subprocess, sys
from chronicreg import ChronicRegistry
from lakefeed import registry_to_events, silver_omop, gold_cascade, feed

def reg():
    r = ChronicRegistry()
    for i in range(6):
        r.enrol("hiv", f"P{i}", "2026-01-0" + str(i + 1))
    for i in range(4):
        r.advance("hiv", f"P{i}", f"ev-{i}", "2026-02-01")
    return r

def test_events_shape_matches_lakehouse():
    evs = registry_to_events(reg().members, facility_of=lambda p: "F1")
    assert all(set(("id", "ts", "facility", "type", "payload")) <= set(e) for e in evs)
    types = {e["type"] for e in evs}
    assert "registry_enrolment" in types and "stage_transition" in types

def test_omop_silver():
    adh = {("hiv", "P0"): (28, 30, "2026-03")}
    omop = silver_omop(registry_to_events(reg().members, adh, lambda p: "F1"))
    assert len(omop["person"]) == 6
    assert len(omop["condition_occurrence"]) == 6
    assert omop["observation"][0]["observation"] == "hiv_adherence"

def test_gold_cascade_latest_stage():
    gold = gold_cascade(registry_to_events(reg().members, facility_of=lambda p: "F1"))
    cas = gold["cascades"]["hiv@F1"]
    assert cas["total"] == 6 and cas["stages"]["linked_to_care"] == 4

def test_k_anonymity_suppression():
    adh = {("hiv", "P0"): (28, 30, "2026-03")}  # n=1 < k
    gold = gold_cascade(registry_to_events(reg().members, adh, lambda p: "F1"))
    assert gold["adherence"]["hiv@F1"]["suppressed"] is True

def test_feed_end_to_end(tmp_path):
    out = feed(reg().members, str(tmp_path), facility_of=lambda p: "F1")
    assert out["events"] > 0 and out["bronze_rows"] == out["events"]
    assert os.path.exists(os.path.join(str(tmp_path), "gold", "chronic_cascade.json"))

def test_cli(tmp_path):
    p = subprocess.run([sys.executable, "lakefeed.py"],
        input=json.dumps({"members": [{"registry": "hiv", "patient_id": "P1",
                                       "stage": "diagnosed", "enrolled": "2026-01-01"}],
                          "out": str(tmp_path), "facility": "F1"}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert p.returncode == 0, p.stderr
