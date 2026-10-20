import json, subprocess, sys, os, pytest
from facilityreg import FacilityRegistry, DuplicateSuspect

def test_mint_unique_ids():
    r = FacilityRegistry()
    a = r.mint("Ikeja PHC", "lagos", "ikeja", "phc")
    b = r.mint("Ikeja General", "lagos", "ikeja", "hospital")
    assert a["facility_id"] == "OHF-LG-IKE-0001" and b["facility_id"] == "OHF-LG-IKE-0002"

def test_dhis2_uid_unique():
    r = FacilityRegistry()
    r.mint("Kano PHC", "kano", "kano", "phc", dhis2_uid="abc123")
    with pytest.raises(DuplicateSuspect):
        r.mint("Kano PHC Dup", "kano", "kano", "phc", dhis2_uid="abc123")

def test_name_lga_duplicate_flagged_not_blocked():
    r = FacilityRegistry()
    r.mint("St Mary", "lagos", "ikeja", "phc")
    r.mint("St Mary", "lagos", "ikeja", "phc")  # mints but flags
    assert len(r.duplicate_flags) == 1

def test_dhis2_inbound_upsert():
    r = FacilityRegistry()
    out1 = r.import_dhis2_orgunits([{"id": "u1", "name": "Wuse PHC", "state": "abuja", "lga": "amc"}])
    out2 = r.import_dhis2_orgunits([{"id": "u1", "name": "Wuse PHC Renamed"}])
    assert out1["created"] == 1 and out2["updated"] == 1
    assert r.get(r.resolve_external("u1"))["name"] == "Wuse PHC Renamed"

def test_cli():
    p = subprocess.run([sys.executable, "facilityreg.py", "import-dhis2"],
        input=json.dumps({"org_units": [{"id": "u1", "name": "PHC A", "state": "lagos", "lga": "eti-osa"}]}),
        capture_output=True, text=True, cwd=os.path.dirname(__file__))
    assert json.loads(p.stdout)["created"] == 1
