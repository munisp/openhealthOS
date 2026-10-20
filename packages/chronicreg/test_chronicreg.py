import json, subprocess, sys, os, pytest
from chronicreg import ChronicRegistry, RegistryError, adherence_score, adherence_band, hiv_cascade_report

def test_enrol_and_advance():
    r = ChronicRegistry()
    r.enrol("hiv", "P1", "2026-01-01")
    r.advance("hiv", "P1", "visit-1", "2026-01-05")
    assert r.members[("hiv", "P1")]["stage"] == "linked_to_care"

def test_advance_requires_evidence():
    r = ChronicRegistry()
    r.enrol("hiv", "P1", "2026-01-01")
    with pytest.raises(RegistryError):
        r.advance("hiv", "P1", "", "2026-01-05")

def test_no_double_enrol():
    r = ChronicRegistry()
    r.enrol("diabetes", "P1", "2026-01-01")
    with pytest.raises(RegistryError):
        r.enrol("diabetes", "P1", "2026-01-02")

def test_final_stage_guard():
    r = ChronicRegistry()
    r.enrol("hypertension", "P1", "2026-01-01")
    r.advance("hypertension", "P1", "rx", "2026-02-01")
    r.advance("hypertension", "P1", "bp<140/90", "2026-03-01")
    with pytest.raises(RegistryError):
        r.advance("hypertension", "P1", "x", "2026-04-01")

def test_adherence_bands():
    assert adherence_score(28, 30) == pytest.approx(0.933)
    assert adherence_band(0.97) == "high" and adherence_band(0.5) == "low"
    assert adherence_score(5, 0) == 0.0

def test_hiv_cascade_report():
    members = [{"stage": "diagnosed"}, {"stage": "on_art"}, {"stage": "virally_suppressed"}]
    rep = hiv_cascade_report(members)
    assert rep["pct_suppressed"] == pytest.approx(33.3)

def test_cli():
    p = subprocess.run([sys.executable, "chronicreg.py"],
        input=json.dumps({"members": [{"stage": "on_art"}]}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["pct_on_art"] == 100.0
