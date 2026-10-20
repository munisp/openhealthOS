import json, subprocess, sys, os
from anc import contact_status, triage_danger, partograph_alert, edd

def test_edd_naegele():
    assert edd("2026-01-01") == "2026-10-08"

def test_contact_status_defaulter():
    s = contact_status("2026-01-01", [], as_of="2026-10-20")  # ~41 weeks
    assert s["defaulter"] and s["overdue"] >= 6 and not s["post_term"]

def test_contacts_done():
    s = contact_status("2026-06-01", [12, 20], as_of="2026-10-20")  # ~20 wk
    done = [c for c in s["contacts"] if c["status"] == "DONE"]
    assert len(done) == 2

def test_danger_triage_critical_first():
    out = triage_danger(["fever", "vaginal_bleeding"])
    assert out[0]["severity"] == "critical" and "bleeding" in out[0]["sign"]

def test_unknown_sign_honest():
    out = triage_danger(["weird_sign"])
    assert out[0]["severity"] == "info" and "Unmapped" in out[0]["action"]

def test_partograph_alert_line():
    assert partograph_alert([(0, 4), (4, 5)])["alert"]  # 0.25 cm/hr
    assert not partograph_alert([(0, 4), (2, 7)])["alert"]  # 1.5 cm/hr

def test_cli():
    p = subprocess.run([sys.executable, "anc.py", "status"],
        input=json.dumps({"lmp": "2026-01-01", "contacts": [], "as_of": "2026-10-20"}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["defaulter"] is True
