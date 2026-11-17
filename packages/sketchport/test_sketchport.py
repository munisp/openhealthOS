"""Contract tests for sketchport (ADR-0090)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import sketchport  # noqa: E402


def _events(pairs):
    """pairs: [(facility, patient_id, program)]"""
    return [{"id": "e%03d" % i, "ts": "2026-11-13T00:00:%02dZ" % (i % 60),
             "facility": f, "type": "registry_enrolment",
             "payload": {"patient_id": p, "program": g}}
            for i, (f, p, g) in enumerate(pairs)]


class TestMainAdapter(unittest.TestCase):
    """Main means main: the wheel absent is a red gate, not a quiet
    fallback (ADR-0090)."""

    def test_adapter_is_main(self):
        self.assertEqual(sketchport.ADAPTER, sketchport.ADAPTER_MAIN)
        self.assertEqual(sketchport.ADAPTER, "datasketches")

    def test_not_degraded(self):
        self.assertFalse(sketchport.DEGRADED)
        self.assertIsNone(sketchport.DEGRADED_REASON)

    def test_engine_version_read(self):
        self.assertIsNotNone(sketchport.DATASKETCHES_VERSION)

    def test_constants_are_data(self):
        self.assertEqual(sketchport.SEED, 20261114)
        self.assertEqual(sketchport.BOUNDS_STD_DEVS, 2)
        self.assertEqual(sketchport.K_FLOOR, 5)
        self.assertEqual(sketchport.SKETCH_KINDS, ("theta", "kll"))


class TestThetaContract(unittest.TestCase):
    def test_distinct_estimate_and_bounds(self):
        cell = sketchport.new_cell("theta")
        for i in range(500):
            cell.update("p-%04d" % i)
        est = cell.estimate()
        lo, hi = cell.bounds()
        self.assertLessEqual(lo, 500)
        self.assertGreaterEqual(hi, 500)
        self.assertLess(abs(est - 500) / 500, 0.02)

    def test_duplicate_updates_dedup(self):
        cell = sketchport.new_cell("theta")
        for i in range(100):
            cell.update("same-patient")
        self.assertAlmostEqual(cell.estimate(), 1.0, delta=0.1)

    def test_serialize_deterministic_given_seed(self):
        a, b = sketchport.new_cell("theta"), sketchport.new_cell("theta")
        for i in range(200):
            a.update("p-%d" % i)
            b.update("p-%d" % i)
        self.assertEqual(a.serialize(), b.serialize())
        self.assertEqual(a.digest(), b.digest())

    def test_deserialize_roundtrip_and_seed_gate(self):
        cell = sketchport.new_cell("theta")
        for i in range(50):
            cell.update("p-%d" % i)
        back = sketchport.deserialize_theta(cell.serialize())
        self.assertEqual(back.get_estimate(), cell.estimate())
        with self.assertRaises(sketchport.SketchRefusal) as ctx:
            sketchport.deserialize_theta(cell.serialize(),
                                         seed=sketchport.SEED + 1)
        self.assertIn("seed hash", str(ctx.exception))

    def test_merge_deduplicates_overlap(self):
        a, b = sketchport.new_cell("theta"), sketchport.new_cell("theta")
        for i in range(100):
            a.update("p-%d" % i)
        for i in range(50, 150):
            b.update("p-%d" % i)
        a.merge(b)
        self.assertAlmostEqual(a.estimate(), 150.0, delta=3.0)

    def test_merge_is_byte_mergeable(self):
        """Serialized bytes alone carry the merge — no raw rows."""
        a, b = sketchport.new_cell("theta"), sketchport.new_cell("theta")
        a.update("x"); b.update("x"); b.update("y")
        merged = sketchport.new_cell("theta")
        merged.merge(a)
        merged.merge(sketchport.deserialize_theta(b.serialize()))
        self.assertAlmostEqual(merged.estimate(), 2.0, delta=0.1)


class TestSetOperations(unittest.TestCase):
    def setUp(self):
        self.a = sketchport.new_cell("theta")
        self.b = sketchport.new_cell("theta")
        for i in range(1600):
            self.a.update("p-%04d" % i)
        for i in range(1000, 2100):
            self.b.update("p-%04d" % i)

    def test_intersection(self):
        r = sketchport.set_op("program-cohort", "intersection",
                              self.a, self.b)
        self.assertLessEqual(r["lower"], 600)
        self.assertGreaterEqual(r["upper"], 600)
        self.assertEqual(r["adapter"], "datasketches")

    def test_a_not_b(self):
        r = sketchport.set_op("program-cohort", "a_not_b", self.a, self.b)
        self.assertLessEqual(r["lower"], 1000)
        self.assertGreaterEqual(r["upper"], 1000)

    def test_union(self):
        r = sketchport.set_op("program-cohort", "union", self.a, self.b)
        self.assertLessEqual(r["lower"], 2100)
        self.assertGreaterEqual(r["upper"], 2100)

    def test_unknown_op_refused(self):
        with self.assertRaises(sketchport.SketchRefusal) as ctx:
            sketchport.set_op("program-cohort", "xor", self.a, self.b)
        self.assertIn("xor", str(ctx.exception))


class TestKllContract(unittest.TestCase):
    def test_quantile_verdict_within_rank_error(self):
        cell = sketchport.new_cell("kll")
        values = [float(i % 997) for i in range(10000)]
        for v in values:
            cell.update(v)
        truth = sorted(values)[int(round(0.5 * (len(values) - 1)))]
        window = 2 * cell.rank_error() * (max(values) - min(values))
        self.assertLessEqual(abs(cell.quantile(0.5) - truth), window)

    def test_kll_is_marked_non_replayable(self):
        self.assertFalse(sketchport.REGISTRY["claim-amounts"]["replayable"])
        cell = sketchport.new_cell("kll")
        self.assertFalse(cell.replayable)

    def test_kll_merge(self):
        a, b = sketchport.new_cell("kll"), sketchport.new_cell("kll")
        for i in range(5000):
            a.update(float(i))
        for i in range(5000, 10000):
            b.update(float(i))
        a.merge(b)
        self.assertAlmostEqual(a.quantile(0.5), 5000.0, delta=300.0)


class TestRefusals(unittest.TestCase):
    def test_unknown_rollup_names_registry(self):
        with self.assertRaises(sketchport.SketchRefusal) as ctx:
            sketchport.get_entry("nope")
        self.assertIn("nope", str(ctx.exception))
        self.assertIn("patient-rollup", str(ctx.exception))

    def test_contractual_refused_by_name(self):
        registry = dict(sketchport.REGISTRY)
        registry["billing-exact"] = dict(
            sketchport.REGISTRY["patient-rollup"], contractual=True)
        with self.assertRaises(sketchport.SketchRefusal) as ctx:
            sketchport.get_entry("billing-exact", registry=registry)
        self.assertIn("billing-exact", str(ctx.exception))
        self.assertIn("contractual", str(ctx.exception))

    def test_unknown_kind_refused(self):
        with self.assertRaises(sketchport.SketchRefusal):
            sketchport.new_cell("bogus")


class TestDegradedMode(unittest.TestCase):
    def test_exact_set_cells_are_exact(self):
        cell = sketchport.new_cell("theta", force_degraded=True)
        for i in range(300):
            cell.update("p-%d" % i)
        cell.update("p-0")  # duplicate
        self.assertEqual(cell.estimate(), 300.0)
        lo, hi = cell.bounds()
        self.assertEqual((lo, hi), (300.0, 300.0))
        self.assertEqual(cell.engine, "stdlib-exact")

    def test_degraded_merge(self):
        a = sketchport.new_cell("theta", force_degraded=True)
        b = sketchport.new_cell("theta", force_degraded=True)
        for i in range(100):
            a.update("p-%d" % i)
        for i in range(50, 150):
            b.update("p-%d" % i)
        a.merge(b)
        self.assertEqual(a.estimate(), 150.0)

    def test_degraded_quantile_exact(self):
        cell = sketchport.new_cell("kll", force_degraded=True)
        for i in range(101):
            cell.update(float(i))
        self.assertEqual(cell.quantile(0.5), 50.0)
        self.assertEqual(cell.rank_error(), 0.0)

    def test_gold_rollups_stamps_adapter(self):
        product = sketchport.gold_rollups(
            _events([("F1", "p-%d" % i, "hiv") for i in range(10)]),
            force_degraded=True)
        self.assertEqual(product["adapter"], "stdlib-exact")
        for cell in product["cells"]:
            self.assertEqual(cell["adapter"], "stdlib-exact")


class TestGoldRollups(unittest.TestCase):
    def setUp(self):
        pairs = []
        for i in range(100):
            pairs.append(("F1", "p-%04d" % i, "hiv"))
        for i in range(50, 150):
            pairs.append(("F2", "p-%04d" % i, "hiv"))
        for i in range(3):
            pairs.append(("F9", "s-%04d" % i, "anc"))
        self.events = _events(pairs)
        self.parents = {"F1": "LGA-A", "F2": "LGA-A", "F9": "LGA-B",
                        "LGA-A": "STATE-X", "LGA-B": "STATE-X"}

    def test_hierarchy_merge_dedup(self):
        product = sketchport.gold_rollups(self.events,
                                          parents=self.parents)
        nat = next(c for c in product["cells"]
                   if c["rollup"] == "patient-rollup"
                   and c["level"] == "national")
        self.assertLessEqual(nat["lower"], 153)
        self.assertGreaterEqual(nat["upper"], 153)
        self.assertIn("sketch_sha256", nat)
        self.assertEqual(nat["seed"], sketchport.SEED)

    def test_k_floor_suppression_stated(self):
        product = sketchport.gold_rollups(self.events,
                                          parents=self.parents)
        f9 = next(c for c in product["cells"]
                  if c["level"] == "facility" and c["key"] == "F9")
        self.assertTrue(f9["suppressed"])
        self.assertIn("k-floor k=5", f9["reason"])
        self.assertNotIn("estimate", f9)

    def test_non_replayable_entries_named_excluded(self):
        product = sketchport.gold_rollups(self.events,
                                          parents=self.parents)
        names = [e["rollup"] for e in product["excluded"]]
        self.assertEqual(names, ["claim-amounts"])
        self.assertIn("non-replayable", product["excluded"][0]["reason"])

    def test_partitioned_cohort_cells(self):
        product = sketchport.gold_rollups(self.events,
                                          parents=self.parents)
        hiv = [c for c in product["cells"]
               if c["rollup"] == "program-cohort"
               and c.get("partition") == "hiv"]
        self.assertTrue(hiv)
        anc = [c for c in product["cells"]
               if c["rollup"] == "program-cohort"
               and c.get("partition") == "anc"]
        self.assertTrue(all(c.get("suppressed") for c in anc))


class TestRegistryHygiene(unittest.TestCase):
    def test_no_contractual_entries_ship(self):
        self.assertFalse(any(e.get("contractual")
                             for e in sketchport.REGISTRY.values()))

    def test_kinds_are_closed(self):
        for e in sketchport.REGISTRY.values():
            self.assertIn(e["kind"], sketchport.SKETCH_KINDS)

    def test_census_shape(self):
        c = sketchport.census()
        self.assertEqual(c["adapter_active"], "datasketches")
        self.assertEqual(c["registry_entries"], 3)
        self.assertEqual(c["kinds"], ["theta", "kll"])
        self.assertEqual(c["replayable_entries"],
                         ["patient-rollup", "program-cohort"])
        self.assertEqual(c["non_replayable_entries"], ["claim-amounts"])
        self.assertEqual(c["seed"], sketchport.SEED)


if __name__ == "__main__":
    unittest.main()
