"""The structure-gate gate (ADR-0077): the demo surface is
deterministic, honestly stamped, every structural attack fails closed
with the offender named, and the stated drills carry their basis. The
gate is the point: shape before range before stamp — the checksum
presumes bytes that deserve it, and bytes that deserve it are
well-formed ones."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock", "gisexport"):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))

import demo_geostructure  # noqa: E402
import gisexport  # noqa: E402


class TestGeostructureProof(unittest.TestCase):
    def test_determinism(self):
        a, b = demo_geostructure.run_demo(), demo_geostructure.run_demo()
        self.assertEqual(a["sha256"], b["sha256"])

    def test_stamp_contract(self):
        body = demo_geostructure.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        body = demo_geostructure.run_demo()
        self.assertEqual(body["surface"], "geostructureproof")
        c = body["control"]["governed_export"]
        self.assertTrue(c["governed"])
        self.assertEqual(c["features"], 3)
        # one MultiPolygon (two closed rings of 5 positions each), one
        # LineString (3), one elevated Point (1): 5 + 5 + 3 + 1 = 14
        self.assertEqual(c["structure_checked"]["geometries"], 3)
        self.assertEqual(c["structure_checked"]["rings"], 2)
        self.assertEqual(c["structure_checked"]["positions"], 14)
        self.assertEqual(c["structure_checked"]["elevations"], 1)
        self.assertEqual(c["coordinates_checked"], 14)
        # the coverage-gap layer carries the geodesy contract (census
        # miss closed) and its unserved cell exists
        gap = body["control"]["coverage_gap_geodesy"]
        self.assertEqual(gap["radius_km"], 6371.0)
        self.assertIn("census miss", gap["supersedes"])
        self.assertEqual(gap["unserved_cells"], 1)
        # the census claims zero private haversines and states the one
        # intentional guarded copy
        census = body["control"]["census"]
        self.assertEqual(census["private_haversines_after_wave16"], 0)
        self.assertEqual(len(census["intentional_copies_stated"]), 1)
        drills = body["drills"]
        self.assertEqual(len(drills), 10)
        self.assertTrue(all(d["result"]["outcome"] == "refused"
                            for d in drills[:7]))
        self.assertTrue(all(d["result"]["outcome"] == "stated"
                            for d in drills[7:]))
        # the smuggled-shape drill names the shape and the offender
        self.assertIn("MultiPoint", drills[3]["result"]["reason"])
        self.assertIn("feature 0", drills[3]["result"]["reason"])
        # the bbox drill names the contradiction
        self.assertIn("does not contain its own geometry",
                      drills[5]["result"]["reason"])
        # the unclosed-ring drill names both positions
        self.assertIn("not closed", drills[0]["result"]["reason"])

    def test_gate_is_real_without_the_demo(self):
        """The estate guard, runnable standalone: malformed shapes
        refuse through collection() itself, not through demo glue."""
        bad = {"type": "Feature",
               "geometry": {"type": "Polygon",
                            "coordinates": [[[3.0, 6.0], [3.1, 6.0],
                                             [3.1, 6.1], [3.0, 6.1]]]},
               "properties": {}}
        with self.assertRaises(gisexport.Error) as ctx:
            gisexport.collection([bad])
        self.assertIn("not closed", str(ctx.exception))
        # and the coverage-gap layer now delegates to the shared contract
        self.assertIs(gisexport.distance_km.__module__, "gisexport")
        fc = gisexport.coverage_gap_layer(
            [{"lat": 10.50, "lng": 7.40}],
            [{"lat": 10.90 + 0.001 * i, "lng": 7.40, "members": 1}
             for i in range(6)], max_km=5.0)
        self.assertIn("openhealth:geodesy", fc)
        self.assertEqual(fc["openhealth:geodesy"]["contract"]
                         ["radius_km"], 6371.0)

    def test_file_emission(self):
        demo_geostructure.main()
        with open(demo_geostructure.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["sha256"],
                         demo_geostructure.run_demo()["sha256"])


if __name__ == "__main__":
    unittest.main()
