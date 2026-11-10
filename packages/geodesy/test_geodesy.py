"""The geodesy-convergence gate (ADR-0075): the demo surface is
deterministic, honestly stamped, and the estate's six distance sites
agree bit-for-bit. The drift guard is the point: routing.py keeps its
own stated GEODESY_CONTRACT (the router never imports geolock), and
this suite makes that duplication honest — the semantic cores must
agree or the suite fails."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock", "routing"):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))

import demo_geodesy  # noqa: E402
import geolock  # noqa: E402
import routing  # noqa: E402


class TestGeodesyProof(unittest.TestCase):
    def test_determinism(self):
        a, b = demo_geodesy.run_demo(), demo_geodesy.run_demo()
        self.assertEqual(a["sha256"], b["sha256"])

    def test_stamp_contract(self):
        body = demo_geodesy.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        body = demo_geodesy.run_demo()
        self.assertEqual(body["surface"], "geodesyproof")
        self.assertEqual(len(body["convergence_matrix"]), 8)
        self.assertTrue(body["control"]["matrix_agrees_bit_for_bit"])
        rows = body["convergence_matrix"]
        self.assertEqual(len({tuple(r["legs_km"]) for r in rows}), 1)
        for r in rows:  # every row digest verifies against its content
            self.assertEqual(r["row_sha256"], hashlib.sha256(json.dumps(
                {"site": r["site"], "legs": r["legs_km"]},
                sort_keys=True, separators=(",", ":"))
                .encode()).hexdigest())
        self.assertTrue(body["control"]["drift_guard"]["pinned_equal"])
        div = body["control"]["divergence"]
        self.assertGreater(div["divergence_ppm"], 1.3)
        self.assertLess(div["divergence_ppm"], 1.5)
        self.assertGreater(div["band_margin_x"], 200)
        # the longest reference leg is genuinely long (Kano-PH ~870 km)
        self.assertGreater(
            body["control"]["reference_leg_km"]["kano-to-port-harcourt"],
            800)
        # antipodal is half the contract circumference, clamped not crashed
        self.assertAlmostEqual(
            body["control"]["reference_leg_km"]["true-antipodal"],
            20015.086796, places=4)
        drills = body["drills"]
        self.assertEqual(len(drills), 7)
        refused = [0, 1, 5]   # metre-space, radius tamper, formula drift
        stated = [2, 3, 4, 6]
        self.assertTrue(all(drills[i]["result"]["outcome"] == "refused"
                            for i in refused))
        self.assertTrue(all(drills[i]["result"]["outcome"] == "stated"
                            for i in stated))
        # the fifth drill states the wave-14 census miss, by name
        self.assertIn("census-miss-stated", drills[4]["result"]["detail"])
        self.assertIn("gisexport", drills[4]["result"]["detail"])
        # the seventh states the census was Python-only
        self.assertIn("cross-runtime-census", drills[6]["result"]["detail"])
        # the runtime census is data on the payload
        rt = body["control"]["runtimes"]
        self.assertEqual(rt["python"]["private_after"], 0)
        self.assertEqual(rt["javascript"]["private_before"], 2)
        self.assertEqual(rt["go"]["private_before"], 2)
        # the metre-space drill names the offender
        self.assertIn("376170.8", drills[0]["result"]["detail"])

    def test_drift_guard_is_real(self):
        """The estate guard, runnable without the demo: the router's
        stated contract and geolock's contract must agree on the
        semantic core. A radius edit in either file fails here."""
        for key in demo_geodesy.DRIFT_CORE_KEYS:
            self.assertEqual(routing.GEODESY_CONTRACT[key],
                             geolock.GEODESY_CONTRACT[key], key)

    def test_file_emission(self):
        demo_geodesy.main()
        with open(demo_geodesy.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["sha256"],
                         demo_geodesy.run_demo()["sha256"])


if __name__ == "__main__":
    unittest.main()
