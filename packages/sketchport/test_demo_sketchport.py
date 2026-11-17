"""Determinism and contract tests for the sketchproof demo."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import demo_sketchport  # noqa: E402
import sketchport  # noqa: E402

SEVERITIES = ["green", "green", "green", "green",
              "amber", "green", "amber", "green"]


class TestDeterminism(unittest.TestCase):
    def test_two_runs_same_bytes(self):
        a = json.dumps(demo_sketchport.run_demo(), sort_keys=True,
                       separators=(",", ":"))
        b = json.dumps(demo_sketchport.run_demo(), sort_keys=True,
                       separators=(",", ":"))
        self.assertEqual(a, b)

    def test_fixture_is_deterministic(self):
        self.assertEqual(len(demo_sketchport.EVENTS),
                         len(demo_sketchport.EVENTS))
        first = demo_sketchport.EVENTS[0]
        self.assertEqual(first["ts"], "2026-11-13T00:00:00Z")
        self.assertEqual(demo_sketchport.TRUTH["national"], 5203)
        self.assertEqual(demo_sketchport.TRUTH["facility_sum"], 6303)

    def test_no_kll_estimates_in_payload(self):
        """kll is non-replayable: verdicts are payload citizens, raw
        quantile estimates are not."""
        body = demo_sketchport.run_demo()
        drill = next(d for d in body["drills"]
                     if d["name"] == "quantile-verdicts-not-estimates")
        self.assertFalse(drill["estimates_in_payload"])
        for verdict in drill["verdicts"].values():
            self.assertEqual(set(verdict), {"within_window"})


class TestStampContract(unittest.TestCase):
    def test_sha_over_compact_body(self):
        body = demo_sketchport.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(sha, expect)
        self.assertEqual(body["self_integrity"], {"contract": "compact"})


class TestPayloadShape(unittest.TestCase):
    def test_eight_drills_exact_severities(self):
        body = demo_sketchport.run_demo()
        self.assertEqual([d["severity"] for d in body["drills"]],
                         SEVERITIES)

    def test_adapter_census_pins(self):
        body = demo_sketchport.run_demo()
        adapter = body["adapter"]
        self.assertEqual(adapter["adapter_active"], "datasketches")
        self.assertFalse(adapter["degraded"])
        self.assertEqual(adapter["registry_entries"], 3)
        self.assertEqual(adapter["seed"], 20261114)
        self.assertEqual(adapter["k_floor"], 5)

    def test_drills_are_real(self):
        body = demo_sketchport.run_demo()
        drills = {d["name"]: d for d in body["drills"]}
        d1 = drills["distinct-with-declared-bounds"]
        self.assertTrue(d1["estimate_within_bounds"])
        self.assertTrue(d1["relative_error_within_2pct"])
        d2 = drills["hierarchy-merge-deduplicates"]
        self.assertTrue(d2["dedup_demonstrated"])
        self.assertGreater(d2["facility_sum"], d2["national_truth"])
        d3 = drills["cohort-set-operations"]
        self.assertTrue(d3["intersection_within_bounds"])
        self.assertTrue(d3["a_not_b_within_bounds"])
        d4 = drills["quantile-verdicts-not-estimates"]
        self.assertTrue(d4["all_verdicts_green"])
        d5 = drills["k-floor-suppression-stated"]
        self.assertTrue(d5["facility_f4_suppressed"])
        self.assertIn("k-floor k=5", d5["reason"])
        d6 = drills["stated-refusals"]
        self.assertTrue(d6["all_refused"])
        self.assertIn("seed hash", d6["refusals"]["wrong_seed"])
        d7 = drills["degraded-mode-is-declared"]
        self.assertEqual(d7["adapter"], "stdlib-exact")
        self.assertTrue(d7["national_estimate_exact"])
        d8 = drills["lakehouse-adopts-the-seam"]
        self.assertTrue(d8["gold_product_written"])
        self.assertTrue(d8["national_matches_direct_drill"])
        self.assertEqual(d8["excluded_stated"][0]["rollup"], "claim-amounts")

    def test_demo_version_and_clock(self):
        body = demo_sketchport.run_demo()
        self.assertEqual(body["demo"], "sketchproof-demo@1.0.0")
        self.assertEqual(body["generated_at"], "2026-11-14T00:00:00Z")


class TestFileEmission(unittest.TestCase):
    def test_main_writes_compact_stamped_payload(self):
        demo_sketchport.main()
        with open(demo_sketchport.OUT) as fh:
            raw = fh.read()
        body = json.loads(raw)
        self.assertEqual(raw, json.dumps(body, sort_keys=True,
                                         separators=(",", ":")))
        sha = body.pop("sha256")
        recomputed = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(sha, recomputed)


class TestLakehouseAdoption(unittest.TestCase):
    def test_build_with_sketch_rollups_writes_gold_product(self):
        import tempfile
        sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                        "..", "lakehouse"))
        import lakehouse
        events = demo_sketchport.EVENTS[:200]
        with tempfile.TemporaryDirectory() as td:
            result = lakehouse.build(events, td, sketch_rollups=True,
                                     sketch_parents=demo_sketchport.PARENTS)
            path = os.path.join(td, "gold", "sketch_rollups.json")
            self.assertTrue(os.path.exists(path))
            with open(path) as fh:
                product = json.load(fh)
        self.assertEqual(product["adapter"], "datasketches")
        self.assertIn("sketch_rollups", result["gold"])
        self.assertTrue(any(c["rollup"] == "patient-rollup"
                            for c in product["cells"]))


if __name__ == "__main__":
    unittest.main()
