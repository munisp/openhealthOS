import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import demo_geoint  # noqa: E402


class TestDemoGeointproof(unittest.TestCase):

    def test_demo_is_deterministic(self):
        self.assertEqual(demo_geoint.run_demo(), demo_geoint.run_demo())

    def test_stamp_contract(self):
        body = demo_geoint.run_demo()
        stamp = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(stamp, expect)

    def test_payload_shape(self):
        body = demo_geoint.run_demo()
        self.assertEqual(body["surface"], "geointproof")
        self.assertTrue(body["demo"])
        self.assertEqual(len(body["drills"]), 6)
        outcomes = [d["result"]["outcome"] for d in body["drills"]]
        self.assertEqual(outcomes[:4], ["refused"] * 4)
        self.assertEqual(outcomes[4:], ["stated"] * 2)
        for d in body["drills"]:
            self.assertNotEqual(d["result"]["outcome"],
                                "NOT REFUSED — DEFECT")
        ctrl = body["control"]
        self.assertEqual(ctrl["preflight"]["known_answers"], "pinned")
        self.assertTrue(ctrl["facilities_export"]["governed"])
        self.assertEqual(ctrl["density_layer"]["centroid_space"],
                         "utm-metres")
        self.assertEqual(ctrl["density_layer"]["centroid_crs"],
                         "EPSG:32631")
        self.assertTrue(ctrl["privacy_floor"]["suppressed_sparse_cell"])
        self.assertEqual(ctrl["road_graph"]["basis"],
                         "registry-governed")
        self.assertEqual(len(ctrl["road_graph"]["source_sha256"]), 64)
        self.assertEqual(len(ctrl["route"]["route_sha256"]), 64)
        self.assertGreater(ctrl["route"]["distance_km"], 1.8)

    def test_file_emission(self):
        demo_geoint.main()
        with open(demo_geoint.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, demo_geoint.run_demo())


if __name__ == "__main__":
    unittest.main()
