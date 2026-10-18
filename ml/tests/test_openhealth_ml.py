"""Tests for openhealth_ml (ADR-0014).

Two tiers, mirroring the stack:
* pure-python contract tests — data, features, registry, metrics, A/B,
  drift. These run everywhere, including the stdlib-only CI lane.
* torch tests — model shapes, a real training smoke, champion loading.
  Skipped cleanly when torch is absent (CI has a dedicated torch lane).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from openhealth_ml import ab, catchment as C, data as D, features as F, monitor, registry as R
from openhealth_ml.models import TORCH
from openhealth_ml.train import auc_roc, pr_auc


class TestData(unittest.TestCase):
    def test_generator_shape_and_calibration(self):
        claims = D.generate_claims(1000, seed=1)
        self.assertEqual(len(claims), 1000)
        rate = sum(1 for c in claims if c["fraud"]) / 1000
        self.assertTrue(0.03 < rate < 0.10, rate)
        # archetypes all exercised, honest labels attached
        archs = {c["archetype"] for c in claims if c["fraud"]}
        self.assertEqual(archs, set(D.FRAUD_ARCHETYPES))
        # malaria dominates — the calibration claim
        top = max(D.CODES, key=lambda c: sum(1 for x in claims if x["code"] == c))
        self.assertEqual(top, "MAL-RDT")

    def test_lakehouse_reader_roundtrip(self):
        claims = D.generate_claims(50, seed=2)
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "claims.jsonl")
            with open(p, "w") as f:
                for c in claims:
                    f.write(__import__("json").dumps(c) + "\n")
            loaded = D.load_lakehouse_claims(p)
            self.assertEqual(len(loaded), 50)
        finally:
            shutil.rmtree(d)
        self.assertIsNone(D.load_lakehouse_claims("/nonexistent.jsonl"))

    def test_dataset_hash_sensitive_to_membership(self):
        a = D.generate_claims(100, seed=3)
        self.assertEqual(D.dataset_hash(a), D.dataset_hash(list(a)))
        self.assertNotEqual(D.dataset_hash(a), D.dataset_hash(a[:-1]))


class TestFeatures(unittest.TestCase):
    def test_feature_shape(self):
        claims = D.generate_claims(100, seed=4)
        rows = F.build_features(claims)
        self.assertEqual(len(rows), 100)
        self.assertTrue(all(len(r) == F.NUM_FEATURES == 10 for r in rows))

    def test_graph_is_bipartite_and_supervised(self):
        claims = D.generate_claims(400, seed=5)
        g = F.build_graph(claims)
        n = len(g["x"])
        self.assertEqual(n, g["n_fac"] + len({c["member_id"] for c in claims}))
        # no facility-facility or member-member edges
        for i in range(g["n_fac"]):
            for j in range(g["n_fac"]):
                self.assertEqual(g["adj"][i][j], 0.0)
        # velocity facility must be labelled positive
        vel = g["facility_ids"].index("PHC-VELOCITY")
        self.assertEqual(g["y"][vel], 1)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    def _register(self, auc, name="fraud-mlp"):
        w = os.path.join(self.root, f"w{auc}.pt")
        with open(w, "wb") as f:
            f.write(b"weights")
        return R.register(self.root, name, w, {"holdout_auc_roc": auc},
                          {"data_hash": "abc"})

    def test_register_promote_load(self):
        v1 = self._register(0.90)
        self.assertEqual(v1, "v1")
        self.assertIsNone(R.champion(self.root, "fraud-mlp"))
        R.promote(self.root, "fraud-mlp", v1)
        self.assertEqual(R.champion(self.root, "fraud-mlp"), "v1")
        meta = R.load_champion(self.root, "fraud-mlp")
        self.assertEqual(meta["metrics"]["holdout_auc_roc"], 0.90)
        v2 = self._register(0.95)
        self.assertEqual(v2, "v2")

    def test_promote_rejects_unknown_version(self):
        with self.assertRaises(ValueError):
            R.promote(self.root, "fraud-mlp", "v99")


class TestMetrics(unittest.TestCase):
    def test_auc_known_values(self):
        self.assertEqual(auc_roc([0, 1], [0.1, 0.9]), 1.0)
        self.assertEqual(auc_roc([0, 1], [0.9, 0.1]), 0.0)
        self.assertAlmostEqual(auc_roc([0, 1], [0.5, 0.5]), 0.5)
        self.assertEqual(auc_roc([1, 1], [0.1, 0.2]), 0.5)  # no negatives

    def test_pr_auc_bounds(self):
        p = pr_auc([0] * 50 + [1] * 50,
                   [i / 100 for i in range(100)])
        self.assertTrue(0.0 <= p <= 1.0, p)


class TestGovernance(unittest.TestCase):
    def test_choose_threshold_respects_budget(self):
        from openhealth_ml.train import choose_threshold
        # separable case: fraud scores high
        y = [0] * 90 + [1] * 10
        s = [0.05] * 90 + [0.95] * 10
        dt = choose_threshold(y, s, review_budget=0.10, min_precision=0.8)
        self.assertEqual(dt["precision"], 1.0)
        self.assertEqual(dt["recall"], 1.0)
        self.assertIn("precision>=0.8", dt["policy"])

    def test_choose_threshold_fallback_is_declared(self):
        from openhealth_ml.train import choose_threshold
        # 5 negatives out-score every positive: within a 10% review budget,
        # precision 0.99 is genuinely infeasible — the policy must SAY so.
        y = [0] * 90 + [0] * 5 + [1] * 5
        s = [0.2] * 90 + [0.9] * 5 + [0.85] * 5
        dt = choose_threshold(y, s, review_budget=0.10, min_precision=0.99)
        self.assertIn("fallback", dt["policy"])
        self.assertLess(dt["precision"], 0.99)

    def test_calibration_table_shape(self):
        from openhealth_ml.train import calibration_table
        y = [0, 0, 0, 1, 1]
        s = [0.05, 0.15, 0.25, 0.85, 0.95]
        table = calibration_table(y, s)
        self.assertTrue(all(set(r) == {"bin", "n", "mean_score", "fraud_rate"} for r in table))
        top = table[-1]
        self.assertEqual(top["fraud_rate"], 1.0)

    def test_per_archetype_recall(self):
        from openhealth_ml.train import per_archetype_recall
        claims = [{"archetype": "ghost", "fraud": True},
                  {"archetype": "ghost", "fraud": True},
                  {"archetype": "upcoding", "fraud": True},
                  {"archetype": "", "fraud": False}]
        idx = [0, 1, 2, 3]
        r = per_archetype_recall(claims, idx, [1, 1, 1, 0], [0.9, 0.1, 0.8, 0.1], 0.5)
        self.assertEqual(r["ghost"], 0.5)
        self.assertEqual(r["upcoding"], 1.0)
        self.assertNotIn("", r)


class TestRings(unittest.TestCase):
    def test_rings_find_shared_member_components(self):
        from openhealth_ml.rings import find_rings
        claims = [
            # ring: FAC-A and FAC-B share member M1
            {"facility_id": "FAC-A", "member_id": "M1", "fraud": True},
            {"facility_id": "FAC-B", "member_id": "M1", "fraud": True},
            {"facility_id": "FAC-A", "member_id": "M2", "fraud": False},
            # isolated facility — not a ring
            {"facility_id": "FAC-C", "member_id": "M9", "fraud": False},
        ]
        rings = find_rings(claims)
        self.assertEqual(len(rings), 1)
        ring = rings[0]
        self.assertEqual(sorted(ring["facilities"]), ["FAC-A", "FAC-B"])
        self.assertIn("M1", ring["shared_members"])
        self.assertEqual(ring["fraud_claims"], 2)

    def test_fraud_only_isolates_ring_from_giant_component(self):
        from openhealth_ml.rings import find_rings
        claims = []
        # Background sharing merges everything into one component...
        for i in range(6):
            claims.append({"facility_id": f"F{i}", "member_id": f"S{i}", "fraud": False})
            claims.append({"facility_id": f"F{i+1}", "member_id": f"S{i}", "fraud": False})
        # ...but the fraud edges only involve F0/F1.
        claims[0]["fraud"] = True
        claims[1]["fraud"] = True
        full = find_rings(claims, min_facilities=2)
        fraud_only = find_rings(claims, min_facilities=2, fraud_only=True)
        self.assertGreater(full[0]["n_facilities"], fraud_only[0]["n_facilities"])
        self.assertEqual(sorted(fraud_only[0]["facilities"]), ["F0", "F1"])
        self.assertEqual(fraud_only[0]["fraud_share"], 1.0)


class TestCatchment(unittest.TestCase):
    def _facs(self):
        return [
            {"id": "A", "name": "A PHC", "state": "S1", "lga": "LGA-1",
             "latitude": 9.0, "longitude": 7.0, "beds": 10, "doctors": 1, "nurses": 4},
            {"id": "B", "name": "B PHC", "state": "S1", "lga": "LGA-1",
             "latitude": 9.05, "longitude": 7.0, "beds": 0, "doctors": 0, "nurses": 1},
            {"id": "C", "name": "C outreach", "state": "S2", "lga": "LGA-2",
             "latitude": None, "longitude": None, "beds": 4, "doctors": 0, "nurses": 1},
        ]

    def test_haversine_matches_registry_constant(self):
        # ~111.19 km per degree of latitude at the equator-ish; the exact
        # value must agree with the shared earth radius (6371.0088 km).
        d = C.haversine_km(9.0, 7.0, 10.0, 7.0)
        self.assertAlmostEqual(d, 111.195, places=2)

    def test_assign_nearest_skips_ungeolocated(self):
        m = {"latitude": 9.0, "longitude": 7.001}
        f, d = C.assign_nearest(m, self._facs())
        self.assertEqual(f["id"], "A")  # C is closer on paper but unplaced
        self.assertLess(d, 0.2)

    def test_analyze_aggregation_and_underserved(self):
        facs = self._facs()
        members = [{"member_id": f"M{i}", "lga": "LGA-1",
                    "latitude": 9.0 + i * 0.001, "longitude": 7.0} for i in range(100)]
        rep = C.analyze(facs, members)
        row = next(r for r in rep["per_lga"] if r["lga"] == "LGA-1")
        self.assertEqual(row["facilities"], 2)
        self.assertEqual(row["geolocated_facilities"], 2)
        self.assertEqual(row["beds"], 10)
        self.assertEqual(row["members"], 100)
        self.assertAlmostEqual(row["beds_per_1k_members"], 100.0, places=1)
        self.assertIsNotNone(row["avg_distance_km"])
        self.assertEqual(rep["national"]["total_beds"], 14)  # includes ungeolocated C
        # LGA-2 has capacity but no members and no geo — must not crash.
        row2 = next(r for r in rep["per_lga"] if r["lga"] == "LGA-2")
        self.assertFalse(row2["underserved"])

    def test_underserved_flags_no_facility_lga(self):
        members = [{"member_id": "M1", "lga": "NOWHERE",
                    "latitude": 6.0, "longitude": 3.0}]
        rep = C.analyze(self._facs(), members)
        row = next(r for r in rep["per_lga"] if r["lga"] == "NOWHERE")
        self.assertTrue(row["underserved"])
        self.assertIn("members with no facility in LGA", row["reasons"])

    def test_sample_csv_loads(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..",
                            "data", "facilities", "ng-phc-sample.csv")
        facs = C.load_facilities_csv(os.path.abspath(path))
        self.assertGreaterEqual(len(facs), 30)
        self.assertTrue(any(f["latitude"] is None for f in facs))
        self.assertGreater(sum(f["beds"] for f in facs), 1000)


class TestAB(unittest.TestCase):
    def test_deterministic_and_sane_split(self):
        a1 = ab.assign_arm("CLM-000001", 10)
        self.assertEqual(a1, ab.assign_arm("CLM-000001", 10))
        arms = [ab.assign_arm(f"CLM-{i:06d}", 20) for i in range(1000)]
        challenger = arms.count("challenger") / 1000
        self.assertTrue(0.10 < challenger < 0.30, challenger)
        with self.assertRaises(ValueError):
            ab.assign_arm("x", 101)


class TestMonitor(unittest.TestCase):
    def test_psi_zero_for_identical_and_high_for_shifted(self):
        base = [float(i % 20) for i in range(500)]
        self.assertAlmostEqual(monitor.psi(base, list(base)), 0.0, places=2)
        shifted = [v + 15 for v in base]
        self.assertGreater(monitor.psi(base, shifted), monitor.PSI_SIGNIFICANT)

    def test_drift_report_verdicts(self):
        ref = D.generate_claims(500, seed=6)
        cur = D.generate_claims(500, seed=6)
        self.assertEqual(monitor.drift_report(ref, cur)["verdict"], "stable")

    def test_score_window(self):
        w = monitor.score_window([{"score": 0.9, "action": "PEND"},
                                  {"score": 0.1, "action": "PASS"}])
        self.assertEqual(w["pend_rate"], 0.5)
        self.assertEqual(monitor.score_window([]), {"n": 0})


@unittest.skipUnless(TORCH, "torch not installed — stdlib lane")
class TestTorchModels(unittest.TestCase):
    def test_mlp_shapes(self):
        import torch
        from openhealth_ml.models import FraudMLP
        m = FraudMLP()
        x = torch.zeros(4, F.NUM_FEATURES)
        self.assertEqual(m(x).shape, (4,))
        probs = m.score(x)
        self.assertTrue(((probs >= 0) & (probs <= 1)).all())

    def test_gcn_shapes_and_normalisation(self):
        import torch
        from openhealth_ml.models import ClaimGCN
        m = ClaimGCN()
        x = torch.rand(5, 3)
        a = torch.rand(5, 5)
        a = (a + a.t()) / 2
        an = ClaimGCN.normalize_adj(a)
        self.assertEqual(m(x, an).shape, (5,))

    def test_training_smoke_learns(self):
        """A real (small) training run must beat chance on holdout."""
        from openhealth_ml.train import train_fraud_mlp
        claims = D.generate_claims(1500, seed=7)
        result = train_fraud_mlp(claims, epochs=12, seed=7)
        self.assertGreater(result["metrics"]["holdout_auc_roc"], 0.7,
                           result["metrics"])

    def test_champion_scorer_roundtrip(self):
        from openhealth_ml.infer import ChampionScorer
        from openhealth_ml.train import train_fraud_mlp
        root = tempfile.mkdtemp()
        try:
            claims = D.generate_claims(800, seed=8)
            result = train_fraud_mlp(claims, epochs=8, seed=8)
            R.register(root, "fraud-mlp", result["weights"],
                       result["metrics"], {"data_hash": "x"})
            R.promote(root, "fraud-mlp", "v1")
            scorer = ChampionScorer(root)
            out = scorer.score_claims(claims[:10])
            self.assertEqual(len(out), 10)
            self.assertTrue(all(0 <= o["score"] <= 1 for o in out))
            self.assertEqual(out[0]["model_version"], "v1")
        finally:
            shutil.rmtree(root)

    def test_heuristic_fallback_without_champion(self):
        from openhealth_ml.infer import ChampionScorer
        root = tempfile.mkdtemp()
        try:
            scorer = ChampionScorer(root)
            out = scorer.score_claims(D.generate_claims(5, seed=9))
            self.assertEqual(out[0]["model_version"], "heuristic-fallback")
        finally:
            shutil.rmtree(root)

    def test_bridge_scorer_matches_frauddetect_seam(self):
        from openhealth_ml.bridge import scorer_for, to_ml_claim
        root = tempfile.mkdtemp()
        try:
            claims = D.generate_claims(800, seed=11)
            from openhealth_ml.train import train_fraud_mlp
            result = train_fraud_mlp(claims, epochs=6, seed=11)
            R.register(root, "fraud-mlp", result["weights"],
                       result["metrics"], {"data_hash": "x"})
            R.promote(root, "fraud-mlp", "v1")
            scorer = scorer_for(root)
            feed_claim = {"claim_id": "BR-1", "amount_ngn": 5000,
                          "tariff_code": "MAL-RDT", "provider_id": "PHC-0001",
                          "patient_id": "NG-00001", "ts": "2026-10-01T02:00:00Z"}
            r = scorer(feed_claim, [])
            self.assertIsNotNone(r)
            self.assertTrue(0.0 <= r["score"] <= 1.0)
            self.assertEqual(r["model_version"], "v1")
            mc = to_ml_claim(feed_claim)
            self.assertEqual(mc["tariff_ngn"], 1500.0)
            self.assertEqual(mc["hour"], 2)
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
