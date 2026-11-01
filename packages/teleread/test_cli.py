import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")


def run(db, cmd, args=None):
    p = subprocess.run(
        [sys.executable, CLI, db, cmd, json.dumps(args or {})],
        capture_output=True, text=True)
    return p.returncode, json.loads(p.stdout)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "state.json")

    def test_full_flow_persists_across_invocations(self):
        rc, out = run(self.db, "register-reader", {
            "reader_id": "rad-1", "licence_id": "MDCN-1",
            "licence_expiry_day": 400, "scopes": ["chest"]})
        self.assertEqual(out["ok"], True)
        rc, out = run(self.db, "order", {
            "domain": "radiology", "case_id": "S1", "priority": "stat",
            "question": "?pneumothorax", "facility": "phc-1",
            "referrer": "n-1", "day": 0, "scope": "chest",
            "artifact": "dicom-study"})
        self.assertEqual(rc, 0)
        run(self.db, "assign", {"domain": "radiology", "case_id": "S1",
                                "day": 0})
        rc, out = run(self.db, "worklist", {"domain": "radiology",
                                            "reader_id": "rad-1",
                                            "day": 0})
        self.assertEqual(out["result"][0]["case_id"], "S1")
        run(self.db, "draft", {
            "domain": "radiology", "case_id": "S1", "reader_id": "rad-1",
            "findings": "right pneumothorax", "impression": "urgent",
            "day": 0, "critical_findings": ["pneumothorax"],
            "template_version": "cxr-v1"})
        run(self.db, "verify", {"domain": "radiology", "case_id": "S1",
                                "reader_id": "rad-1", "day": 0})
        rc, out = run(self.db, "unack", {"domain": "radiology", "day": 1})
        self.assertEqual(len(out["result"]), 1)
        run(self.db, "ack", {"domain": "radiology", "case_id": "S1",
                             "day": 1, "by": "mo-1"})
        rc, out = run(self.db, "turnaround", {"domain": "radiology",
                                              "day": 1})
        self.assertEqual(out["result"]["by_priority"]["stat"]["n"], 1)
        # state survived N separate processes
        self.assertTrue(os.path.exists(self.db))

    def test_domain_errors_exit_nonzero_loudly(self):
        rc, out = run(self.db, "order", {
            "domain": "radiology", "case_id": "X", "priority": "stat",
            "question": "", "facility": "f", "referrer": "r", "day": 0})
        self.assertEqual(rc, 2)
        self.assertFalse(out["ok"])
        self.assertIn("question", out["error"])

    def test_unknown_domain_and_command_refused(self):
        rc, out = run(self.db, "order", {
            "domain": "astrology", "case_id": "X", "priority": "stat",
            "question": "?", "facility": "f", "referrer": "r", "day": 0})
        self.assertEqual(rc, 2)
        self.assertIn("unknown domain", out["error"])
        rc, out = run(self.db, "levitate", {})
        self.assertEqual(rc, 2)

    def test_missing_field_is_loud(self):
        rc, out = run(self.db, "order", {"domain": "ecg"})
        self.assertEqual(rc, 2)
        self.assertIn("missing field", out["error"])

    def test_domains_listing(self):
        rc, out = run(self.db, "domains")
        self.assertIn("radiology", out["result"])
        self.assertIn("teleicu", out["result"])


if __name__ == "__main__":
    unittest.main()
