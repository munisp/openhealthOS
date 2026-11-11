"""Tests for the adrproof demo surface (ADR-0086)."""

import hashlib
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import adrchain  # noqa: E402
import demo_adrchain  # noqa: E402


class TestDemo(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.body = demo_adrchain.run_demo()

    def test_deterministic(self):
        again = demo_adrchain.run_demo()
        self.assertEqual(self.body, again)
        self.assertEqual(self.body["sha256"], again["sha256"])

    def test_stamp_contract(self):
        body = dict(self.body)
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        self.assertEqual(self.body["version"], "adrproof-demo@1.0.0")
        self.assertEqual(self.body["generated_at"], "2026-11-11T21:00:00Z")
        self.assertEqual(len(self.body["drills"]), 5)
        self.assertEqual([d["severity"] for d in self.body["drills"]],
                         ["green", "red", "amber", "red", "red"])
        self.assertEqual(len(self.body["pre_wave_findings"]["collisions"]), 3)
        self.assertEqual(
            [c["number"] for c in self.body["pre_wave_findings"]["collisions"]],
            ["0060", "0061", "0062"])

    def test_gate_is_real(self):
        # The collision drill must name the colliding number.
        self.assertIn("0060", self.body["drills"][1]["verdict"])
        # The legacy-variant drill must refuse the em-dash form.
        self.assertIn("ADR-0008", self.body["drills"][4]["verdict"])

    def test_live_audit_matches_real_tree(self):
        root = os.path.normpath(os.path.join(_HERE, "..", ".."))
        live = adrchain.audit(root)
        self.assertEqual(self.body["live_audit"]["summary"], live["summary"])
        self.assertTrue(live["summary"]["chain_ok"])

    def test_file_emission(self):
        demo_adrchain.main()
        with open(demo_adrchain.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["sha256"], self.body["sha256"])


if __name__ == "__main__":
    unittest.main()
