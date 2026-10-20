import json, subprocess, sys, os
from secguard import analyze

def reads(actor, day, n, hour=12):
    return [{"ts": f"{day}T{hour:02d}:00:00", "actor": actor, "action": "read",
             "patient_id": f"P{i}"} for i in range(n)]

def test_clean_stream():
    out = analyze(reads("nurse-1", "2026-10-20", 10))
    assert out["clean"] and out["events_scanned"] == 10

def test_mass_export_flagged():
    out = analyze(reads("clerk-7", "2026-10-20", 60))
    assert any(f["rule"] == "mass-export" and f["severity"] == "critical"
               for f in out["findings"])

def test_off_hours_bulk():
    out = analyze(reads("clerk-7", "2026-10-20", 30, hour=23))
    assert any(f["rule"] == "off-hours-bulk" for f in out["findings"])

def test_breakglass_burst():
    evs = [{"ts": f"2026-10-20T{h:02d}:00:00", "actor": "dr-1", "action": "read",
            "break_glass": True, "patient_id": f"P{h}"} for h in range(5)]
    out = analyze(evs)
    assert any(f["rule"] == "breakglass-burst" for f in out["findings"])

def test_scope_probing():
    evs = [{"ts": "2026-10-20T10:00:00", "actor": "temp-1", "action": "read",
            "status": 403}] * 12
    out = analyze(evs)
    assert any(f["rule"] == "scope-escalation" for f in out["findings"])

def test_dormant_sudden_export():
    evs = reads("acct-9", "2026-08-01", 5) +           [{"ts": "2026-10-15T10:00:00", "actor": "acct-9", "action": "export",
            "patient_id": f"P{i}"} for i in range(30)]
    out = analyze(evs)
    assert any(f["rule"] == "dormant-sudden" for f in out["findings"])

def test_rules_are_tunable():
    out = analyze(reads("nurse-1", "2026-10-20", 60),
                  {"mass_export_threshold": 500})
    assert out["clean"]

def test_cli_exit_code():
    p = subprocess.run([sys.executable, "secguard.py"],
        input=json.dumps({"events": reads("x", "2026-10-20", 60)}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert p.returncode == 1
