"""test_demo_ledgergate.py — ledgerproof demo contract tests (ADR-0087)."""

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import demo_ledgergate  # noqa: E402
import ledgergate  # noqa: E402


class TestDemoContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = demo_ledgergate.run_demo()

    def test_deterministic(self):
        self.assertEqual(self.body, demo_ledgergate.run_demo())

    def test_stamp_is_the_contract(self):
        body = dict(self.body)
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        for key in ("version", "generated_at", "gate", "pre_wave_findings",
                    "live_audit", "drills", "doctrine", "sha256"):
            self.assertIn(key, self.body)
        self.assertEqual(self.body["version"], "ledgerproof-demo@1.0.0")
        self.assertEqual(len(self.body["drills"]), 5)
        self.assertEqual([d["severity"] for d in self.body["drills"]],
                         ["green", "red", "amber", "red", "red"])

    def test_drills_are_real(self):
        # drill 2 replays the row-30 collision and names the claimants
        self.assertIn("30", self.body["drills"][1]["verdict"])
        # drill 4 replays the displaced block
        self.assertIn("85", self.body["drills"][3]["verdict"])
        # drill 5 replays the orphaned sub-row
        self.assertIn("71a", self.body["drills"][4]["verdict"])

    def test_live_audit_matches_real_tree(self):
        fresh = ledgergate.audit(demo_ledgergate.REPO_ROOT)
        self.assertEqual(self.body["live_audit"]["summary"], fresh["summary"])
        self.assertTrue(fresh["summary"]["ledger_ok"])

    def test_file_emission(self):
        demo_ledgergate.main()
        with open(demo_ledgergate.OUT, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, self.body)


if __name__ == "__main__":
    unittest.main()
