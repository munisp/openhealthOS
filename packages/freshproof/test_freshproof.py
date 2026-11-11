"""Tests for freshproof — the freshness-gate demo surface (ADR-0080)."""
import hashlib
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))

import demo_freshproof  # noqa: E402
import proofindex  # noqa: E402


class TestFreshProof(unittest.TestCase):
    def test_determinism(self):
        a = demo_freshproof.run_demo()
        b = demo_freshproof.run_demo()
        self.assertEqual(a, b)
        self.assertEqual(a["sha256"], b["sha256"])

    def test_stamp_contract(self):
        body = demo_freshproof.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        p = demo_freshproof.run_demo()
        self.assertEqual(p["version"], "freshproof-demo@1.0.0")
        self.assertEqual(p["census"]["payloads"], 29)
        self.assertEqual(p["census"]["file_family"], 17)
        self.assertEqual(p["census"]["hash_family"], 12)
        self.assertEqual(p["census"]["fresh"], 28)
        self.assertEqual(p["census"]["stale"], 1)
        self.assertEqual(p["census"]["stale_named"],
                         ["apps/pwa/geointproof.json"])
        self.assertEqual(p["census"]["this_wave_regenerations"],
                         ["apps/pwa/shipproof.json"])
        self.assertEqual([d["verdict"] for d in p["drills"]],
                         ["fresh", "stale", "runner-error",
                          "no-exporter", "absent"])
        self.assertEqual(p["report"]["summary"],
                         {"total": 5, "fresh": 1, "stale": 1,
                          "runner_error": 1, "no_exporter": 1,
                          "absent": 1, "red": 3, "amber": 1,
                          "all_fresh": False})

    def test_gate_is_real_stale_names_both_digests(self):
        """The stale verdict must name committed AND fresh sha256 —
        a verdict you cannot audit is decoration."""
        p = demo_freshproof.run_demo()
        stale = p["report"]["payloads"][1]
        self.assertEqual(stale["status"], "stale")
        self.assertRegex(stale["committed_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(stale["fresh_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(stale["committed_sha256"],
                            stale["fresh_sha256"])

    def test_gate_is_real_runner_error_never_crashes(self):
        p = demo_freshproof.run_demo()
        err = p["report"]["payloads"][2]
        self.assertEqual(err["status"], "runner-error")
        self.assertIn("RuntimeError", err["detail"])

    def test_no_exporter_is_stated_not_silent(self):
        p = demo_freshproof.run_demo()
        noop = p["report"]["payloads"][3]
        self.assertEqual(noop["status"], "no-exporter")
        self.assertEqual(noop["severity"], "amber")

    def test_file_emission(self):
        demo_freshproof.main()
        out = os.path.normpath(demo_freshproof.OUT)
        with open(out, encoding="utf-8") as fh:
            back = json.load(fh)
        self.assertEqual(back["version"], demo_freshproof.DEMO_VERSION)
        body = {k: v for k, v in back.items() if k != "sha256"}
        self.assertEqual(back["sha256"], demo_freshproof._stamp(body))


if __name__ == "__main__":
    unittest.main()
