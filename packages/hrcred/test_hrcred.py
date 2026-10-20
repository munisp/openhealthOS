import json, subprocess, sys, os
import pytest
from hrcred import StaffRegistry, CredentialError, COUNCILS

NOW = 1792497600  # 2026-10-20
def reg():
    r = StaffRegistry(now=lambda: NOW)
    r.register("DR-1", "A. Bello", "MDCN-12345", NOW + 365*86400, "F1")
    r.register("RN-1", "B. Chidi", "NMCN-777", NOW + 10*86400, "F1")
    r.register("DR-OLD", "C. Dan", "MDCN-00001", NOW - 1, "F1")
    return r

def test_prescriber_gate():
    r = reg()
    assert r.check_prescriber("DR-1")
    assert not r.check_prescriber("RN-1")   # nurses don't prescribe in this scope
    assert not r.check_prescriber("DR-OLD") # expired licence

def test_suspension():
    r = reg(); r.suspend("DR-1")
    assert not r.check_prescriber("DR-1")

def test_qr_key_eligibility():
    r = reg()
    assert r.qr_key_eligible("DR-1") and r.qr_key_eligible("RN-1")
    assert not r.qr_key_eligible("DR-OLD")

def test_expiring_alerts():
    r = reg()
    exp = r.expiring(within_days=30)
    assert [x["staff_id"] for x in exp] == ["RN-1"]
    assert [x["staff_id"] for x in r.expired()] == ["DR-OLD"]

def test_malformed_licence():
    r = reg()
    with pytest.raises(CredentialError):
        r.register("X", "Y", "BOGUS", NOW+1, "F1")
    with pytest.raises(CredentialError):
        r.register("X", "Y", "WHO-1", NOW+1, "F1")

def test_cli():
    p = subprocess.run([sys.executable, "hrcred.py", "check-prescriber"],
        input=json.dumps({"staff": [{"staff_id":"DR-1","name":"A","licence":"MDCN-1","expires_at":9999999999,"facility_id":"F1"}], "staff_id":"DR-1"}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["ok"] is True
