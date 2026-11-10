"""The cross-runtime geodesy gate (ADR-0078): the demo surface is
deterministic, honestly stamped, the conformance vectors are real
geolock outputs, and the per-runtime claims match what the suites
actually prove — bit-identity where the silicon agrees, a stated
envelope where it does not."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
for _sib in ("geolock",):
    sys.path.insert(0, os.path.join(_HERE, "..", _sib))

import demo_georuntime  # noqa: E402
import geolock  # noqa: E402


class TestGeoruntimeProof(unittest.TestCase):
    def test_determinism(self):
        a, b = demo_georuntime.run_demo(), demo_georuntime.run_demo()
        self.assertEqual(a["sha256"], b["sha256"])

    def test_stamp_contract(self):
        body = demo_georuntime.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        body = demo_georuntime.run_demo()
        self.assertEqual(body["surface"], "georuntimeproof")
        vectors = body["conformance_vectors"]
        self.assertEqual(len(vectors), 5)
        # every vector is a live geolock output — not a pasted constant
        for v in vectors:
            self.assertEqual(v["km"], geolock.distance_km(
                v["a"][0], v["a"][1], v["b"][0], v["b"][1]))
            self.assertIn(v["name"], v and demo_georuntime.GO_EXACT)
        # the stated Go envelope is honest: divergence within 1e-9 km
        for v in vectors:
            self.assertLessEqual(v["go_vs_python_km"], 1e-9)
        # and non-zero somewhere — the envelope is not a disguised
        # bit-identity claim
        self.assertGreater(
            max(v["go_vs_python_km"] for v in vectors), 0.0)
        # the retired atan2 form is measured, not remembered
        self.assertGreater(
            max(v["atan2_vs_contract_km"] for v in vectors), 0.0)
        rt = body["control"]["runtimes"]
        for runtime in ("python", "javascript", "go"):
            self.assertEqual(rt[runtime]["private_after"], 0, runtime)
        self.assertEqual(rt["javascript"]["private_before"], 2)
        self.assertEqual(rt["go"]["private_before"], 2)
        drills = body["drills"]
        self.assertEqual(len(drills), 6)
        self.assertEqual([d["result"]["outcome"] for d in drills],
                         ["refused", "stated", "refused",
                          "stated", "stated", "stated"])
        # refused drills name their proving suite
        self.assertIn("geodesy-conformance.test.mjs",
                      drills[0]["result"]["proof"])
        self.assertIn("376170.8", drills[0]["result"]["detail"])
        # the antipodal drill quotes the contract half-circumference
        self.assertIn("20015.086796020572", drills[1]["result"]["detail"])

    def test_vectors_digest_is_real(self):
        body = demo_georuntime.run_demo()
        pairs = [(v["name"], v["km"]) for v in body["conformance_vectors"]]
        self.assertEqual(body["control"]["vectors_sha256"],
                         hashlib.sha256(json.dumps(
                             pairs, sort_keys=True, separators=(",", ":"))
                             .encode()).hexdigest())

    def test_file_emission(self):
        demo_georuntime.main()
        with open(demo_georuntime.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["sha256"],
                         demo_georuntime.run_demo()["sha256"])


if __name__ == "__main__":
    unittest.main()
