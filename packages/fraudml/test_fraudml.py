import unittest

import gnn
import fraudfusion
import mcmc


def _ring_graph():
    """Two clusters: a collusion ring (F1,F2,P1 sharing patients) and an
    honest cluster (F3,P2). Features: [flag_rate, tariff_z, dup_biometrics]."""
    nodes = ["F1", "F2", "P1", "PT1", "PT2", "F3", "P2", "PT3"]
    feats = [
        [0.9, 0.8, 0.7],  # F1 ring facility
        [0.8, 0.7, 0.6],  # F2 ring facility
        [0.9, 0.6, 0.8],  # P1 ring provider
        [0.5, 0.3, 0.9],  # PT1 shared patient
        [0.4, 0.3, 0.8],  # PT2 shared patient
        [0.05, 0.1, 0.0],  # F3 honest facility
        [0.05, 0.1, 0.1],  # P2 honest provider
        [0.0, 0.0, 0.0],  # PT3 honest patient
    ]
    g = gnn.Graph(nodes, feats)
    for a, b, w in [("F1", "P1", 5), ("F2", "P1", 5), ("F1", "F2", 3),
                    ("F1", "PT1", 4), ("F2", "PT1", 4), ("F1", "PT2", 3),
                    ("F2", "PT2", 3),
                    ("F3", "P2", 5), ("F3", "PT3", 4), ("P2", "PT3", 2)]:
        g.add_edge(a, b, w)
    labels = {"F1": 1, "P1": 1, "F3": 0, "P2": 0, "PT3": 0}
    return g, labels


class TestGraph(unittest.TestCase):
    def test_normalized_adjacency_self_loops(self):
        g = gnn.Graph(["a", "b"], [[1.0], [1.0]])
        g.add_edge("a", "b", 1.0)
        a = g.normalized_adjacency()
        self.assertAlmostEqual(a[0][0], 0.5)
        self.assertAlmostEqual(a[0][1], 0.5)
        self.assertAlmostEqual(a[1][0], 0.5)

    def test_invalid_edges_rejected(self):
        g = gnn.Graph(["a"], [[1.0]])
        with self.assertRaises(ValueError):
            g.add_edge("a", "a")
        with self.assertRaises(ValueError):
            g.add_edge("a", "b")
        with self.assertRaises(ValueError):
            gnn.Graph(["a"], [[1.0], [2.0]])


class TestGCN(unittest.TestCase):
    def test_ring_members_score_higher_than_honest(self):
        g, labels = _ring_graph()
        model = gnn.GCN(g, hidden=8, seed=1, epochs=300, lr=0.3).fit(labels)
        scores = model.ring_scores()
        for ring_node in ("F1", "F2", "P1", "PT1", "PT2"):
            self.assertGreater(scores[ring_node], scores["F3"], ring_node)
            self.assertGreater(scores[ring_node], scores["PT3"], ring_node)

    def test_unlabelled_ring_facility_flagged_by_neighbourhood(self):
        g, labels = _ring_graph()
        model = gnn.GCN(g, hidden=8, seed=1, epochs=300, lr=0.3).fit(labels)
        # F2 never appeared in labels, but its neighbourhood is dirty
        self.assertGreater(model.fraud_probability("F2"), 0.5)

    def test_fit_requires_both_classes(self):
        g, _ = _ring_graph()
        with self.assertRaises(ValueError):
            gnn.GCN(g).fit({"F1": 1, "F2": 1})

    def test_label_propagation_agrees_with_gcn(self):
        g, labels = _ring_graph()
        lp = gnn.label_propagation(g, labels)
        self.assertGreater(lp["F2"], lp["F3"])
        self.assertGreater(lp["PT1"], lp["PT3"])
        self.assertAlmostEqual(lp["F1"], 1.0)  # supervision clamped


class TestFraudFusion(unittest.TestCase):
    def test_strong_multi_signal_auto_pends(self):
        out = fraudfusion.fuse([
            fraudfusion.Signal("rule_engine", 0.9, "ghost-claim pattern"),
            fraudfusion.Signal("gnn_ring", 0.8, "collusion neighbourhood"),
            fraudfusion.Signal("duplicate_biometric", 0.9, "same fingerprint, 3 IDs"),
        ])
        self.assertEqual(out["verdict"], "auto_pend")
        self.assertGreaterEqual(out["probability"], 0.8)
        self.assertIn("gnn_ring", out["contributions"])

    def test_single_weak_signal_clears(self):
        out = fraudfusion.fuse([fraudfusion.Signal("tariff_outlier", 0.4)])
        self.assertEqual(out["verdict"], "clear")
        self.assertLess(out["probability"], 0.5)

    def test_moderate_signals_route_to_review(self):
        out = fraudfusion.fuse([
            fraudfusion.Signal("rule_engine", 0.6),
            fraudfusion.Signal("stock_divergence", 0.5),
        ])
        self.assertEqual(out["verdict"], "review")

    def test_validation(self):
        with self.assertRaises(ValueError):
            fraudfusion.Signal("nonsense", 0.5)
        with self.assertRaises(ValueError):
            fraudfusion.Signal("rule_engine", 1.5)

    def test_fit_learns_separating_weights(self):
        rows = []
        for _ in range(30):
            rows.append(([fraudfusion.Signal("rule_engine", 0.9),
                          fraudfusion.Signal("gnn_ring", 0.8)], 1))
            rows.append(([fraudfusion.Signal("rule_engine", 0.1),
                          fraudfusion.Signal("gnn_ring", 0.1)], 0))
        w = fraudfusion.fit(rows, epochs=300)
        self.assertGreater(w["rule_engine"], 1.0)
        p_fraud = fraudfusion.fuse([fraudfusion.Signal("rule_engine", 0.9),
                                    fraudfusion.Signal("gnn_ring", 0.8)], w)
        p_clean = fraudfusion.fuse([fraudfusion.Signal("rule_engine", 0.1),
                                    fraudfusion.Signal("gnn_ring", 0.1)], w)
        self.assertGreater(p_fraud["probability"], 0.8)
        self.assertLess(p_clean["probability"], 0.2)


class TestMCMC(unittest.TestCase):
    def setUp(self):
        # 40 honest facilities at ~5% flag rate, one bad big facility at 30%,
        # one tiny facility with 1/3 flagged (naive 33% but 3 claims only).
        self.obs = {}
        import random
        rng = random.Random(0)
        for i in range(40):
            n = 200
            x = sum(1 for _ in range(n) if rng.random() < 0.05)
            self.obs[f"H{i:02d}"] = {"claims": n, "flagged": x}
        self.obs["BAD"] = {"claims": 500, "flagged": 150}   # true 30%
        self.obs["TINY"] = {"claims": 3, "flagged": 1}      # raw 33%

    def test_partial_pooling_shrinks_small_samples(self):
        rep = mcmc.facility_report(self.obs, draws=1500, burn=800, seed=3)
        tiny = next(r for r in rep["facilities"] if r["facility"] == "TINY")
        # raw rate is 0.33 but posterior must shrink toward national ~0.05
        self.assertLess(tiny["posterior_mean"], 0.15)
        self.assertFalse(tiny["flag"])

    def test_sustained_bad_actor_flagged(self):
        rep = mcmc.facility_report(self.obs, draws=1500, burn=800, seed=3)
        bad = next(r for r in rep["facilities"] if r["facility"] == "BAD")
        self.assertTrue(bad["flag"])
        self.assertGreater(bad["ci_low"], 0.05)
        self.assertGreater(bad["posterior_mean"], 0.2)

    def test_national_rate_recovered(self):
        rep = mcmc.facility_report(self.obs, draws=1500, burn=800, seed=3)
        self.assertGreater(rep["national_rate_mean"], 0.03)
        self.assertLess(rep["national_rate_mean"], 0.15)

    def test_deterministic_under_seed(self):
        a = mcmc.sample_posterior({"X": {"claims": 10, "flagged": 2}},
                                  draws=200, burn=100, seed=9)
        b = mcmc.sample_posterior({"X": {"claims": 10, "flagged": 2}},
                                  draws=200, burn=100, seed=9)
        self.assertEqual(a["theta"]["X"], b["theta"]["X"])

    def test_credible_interval_bounds(self):
        lo, hi = mcmc.credible_interval([0.01, 0.5, 0.99] * 100)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)

    def test_empty_observations_rejected(self):
        with self.assertRaises(ValueError):
            mcmc.sample_posterior({"X": {"claims": 0, "flagged": 0}})


if __name__ == "__main__":
    unittest.main()
