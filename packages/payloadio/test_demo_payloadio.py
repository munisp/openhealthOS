"""Tests for the atomproof demo surface (ADR-0088)."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "proofindex"))

import demo_payloadio  # noqa: E402
import proofindex  # noqa: E402


def _compact(body: dict) -> str:
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class TestDemoPayloadio(unittest.TestCase):

    def test_determinism(self):
        self.assertEqual(demo_payloadio.run_demo(),
                         demo_payloadio.run_demo())

    def test_stamp_contract(self):
        p = demo_payloadio.run_demo()
        body = {k: v for k, v in p.items() if k != "sha256"}
        self.assertEqual(p["sha256"], _compact(body))
        self.assertRegex(p["sha256"], r"^[0-9a-f]{64}$")

    def test_payload_shape(self):
        p = demo_payloadio.run_demo()
        self.assertEqual(p["demo"], "atomproof-demo@1.0.0")
        self.assertEqual(len(p["drills"]), 5)
        self.assertEqual([d["severity"] for d in p["drills"]],
                         ["green", "red", "green", "green", "green"])
        self.assertEqual(p["campaign"]["raw_writer"]["torn"], 12579)
        self.assertEqual(p["campaign"]["atomic_writer"]["torn"], 0)
        self.assertEqual(p["self_integrity"]["contract"], "compact")

    def test_drills_are_real(self):
        p = demo_payloadio.run_demo()
        d = {d["name"]: d for d in p["drills"]}
        # crash: last-good survived, residue invisible to the scan
        self.assertTrue(d["crash-mid-write"]["survived"])
        self.assertTrue(d["crash-mid-write"]["residue_invisible"])
        # the retired window: paused mid-write, the read WAS torn and
        # the scan DID skip — the exact wave-19 assertion failure
        self.assertTrue(d["torn-read-window"]["torn_read"])
        self.assertTrue(d["torn-read-window"]["scan_skipped"])
        # the seam: last-good during in-flight, new bytes after
        self.assertEqual(d["seam-closes-the-window"]
                         ["read_during_inflight"], 1)
        self.assertEqual(d["seam-closes-the-window"]
                         ["read_after_publish"], 2)
        # the flake replayed under the seam: never skipped
        self.assertEqual(d["wave-19-flake-replayed"]["scans"], 50)
        self.assertEqual(d["wave-19-flake-replayed"]["skipped"], 0)

    def test_adoption_census_matches_live_tree(self):
        p = demo_payloadio.run_demo()
        census = {d["name"]: d for d in p["drills"]}["adoption-census"]
        # post-registration live state (wave-20 gotcha applied):
        # 36 registered payloads + 1 self-excluded = 37; 30 writer
        # modules, all via the seam; the one raw write-open is the
        # declared ledgeranchor tmpdir exception.
        self.assertEqual(census["payloads"], 37)
        self.assertEqual(census["writer_modules"], 30)
        self.assertEqual(census["via_seam"], 30)
        self.assertEqual(census["raw_write_opens"], 4)
        self.assertEqual(census["declared_exceptions"], 2)
        self.assertEqual(census["offenders"], [])

    def test_file_emission(self):
        demo_payloadio.main()
        out = os.path.normpath(demo_payloadio.OUT)
        with open(out, "rb") as fh:
            raw = fh.read()
        back = json.loads(raw)
        self.assertEqual(back["demo"], demo_payloadio.DEMO_VERSION)
        self.assertEqual(raw, json.dumps(
            back, sort_keys=True, separators=(",", ":")).encode())


if __name__ == "__main__":
    unittest.main()
