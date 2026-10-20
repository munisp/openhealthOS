import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "facilityreg"))
from facilityreg import FacilityRegistry
from inbound import map_orgunit, sync_orgunits

UNITS = {"organisationUnits": [
    {"id": "u1", "name": "Wuse PHC", "parent": {"name": "Abuja"}},
    {"id": "u2", "name": "Ikeja PHC", "state": "lagos", "lga": "ikeja", "type": "phc"}]}

class TestInbound(unittest.TestCase):
    def test_map_parent_fallback(self):
        m = map_orgunit(UNITS["organisationUnits"][0])
        self.assertEqual(m["lga"], "Abuja")

    def test_live_sync_creates(self):
        reg = FacilityRegistry()
        out = sync_orgunits(lambda: UNITS, reg)
        self.assertEqual(out["created"], 2)
        self.assertEqual(out["source"], "live")
        self.assertIsNotNone(reg.resolve_external("u1"))

    def test_snapshot_fallback_when_unreachable(self):
        snap = "/tmp/dhis2-snap-test.json"
        reg = FacilityRegistry()
        sync_orgunits(lambda: UNITS, reg, snapshot_path=snap)  # writes snapshot
        reg2 = FacilityRegistry()
        def down(): raise ConnectionError("unreachable")
        out = sync_orgunits(down, reg2, snapshot_path=snap)
        self.assertTrue(out["source"].startswith("snapshot"))
        self.assertEqual(out["created"], 2)
        os.remove(snap)

    def test_repeat_sync_updates_not_duplicates(self):
        reg = FacilityRegistry()
        sync_orgunits(lambda: UNITS, reg)
        out = sync_orgunits(lambda: UNITS, reg)
        self.assertEqual(out["created"], 0)
        self.assertEqual(out["updated"], 2)

if __name__ == "__main__":
    unittest.main()
