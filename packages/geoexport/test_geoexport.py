"""The export-gate gate (ADR-0076): the demo surface is deterministic,
honestly stamped, and every drill outcome is what it claims to be."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import demo_geoexport  # noqa: E402


class TestGeoexportProof(unittest.TestCase):
    def test_determinism(self):
        a, b = demo_geoexport.run_demo(), demo_geoexport.run_demo()
        self.assertEqual(a["sha256"], b["sha256"])

    def test_stamp_contract(self):
        body = demo_geoexport.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        body = demo_geoexport.run_demo()
        self.assertEqual(body["surface"], "geoexportproof")
        c = body["control"]
        self.assertEqual(c["census"]["raw_geojson_sites_after"], 0)
        self.assertEqual(c["governed_export"]["coordinates_checked"], 8)
        self.assertTrue(c["governed_export"]["governed"])
        self.assertFalse(c["privacy_floor"]
                         ["raw_ids_in_anonymized_export"])
        self.assertTrue(c["privacy_floor"]
                        ["identified_export_states_it"])
        drills = body["drills"]
        self.assertEqual(len(drills), 6)
        self.assertTrue(all(d["result"]["outcome"] == "refused"
                            for d in drills[:4]))
        self.assertTrue(all(d["result"]["outcome"] == "stated"
                            for d in drills[4:]))
        # the metre-space drills name the offender
        self.assertIn("376170.8", drills[1]["result"]["detail"])

    def test_file_emission(self):
        demo_geoexport.main()
        with open(demo_geoexport.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["sha256"],
                         demo_geoexport.run_demo()["sha256"])


if __name__ == "__main__":
    unittest.main()
