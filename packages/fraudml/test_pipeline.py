import random
import unittest

import pipeline


def _claims():
    rng = random.Random(5)
    claims = []
    cid = 0
    # honest facility: 100 claims, ~5% flagged
    for _ in range(100):
        cid += 1
        claims.append({"id": f"C{cid}", "facility_id": "GOOD",
                       "provider_id": "DR-A", "patient_id": f"PT{cid % 40}",
                       "flagged": rng.random() < 0.05,
                       "tariff_z": rng.gauss(0, 0.5),
                       "duplicate_biometric": False})
    # bad facility: 120 claims, ~35% flagged, concentrated providers/patients
    for _ in range(120):
        cid += 1
        claims.append({"id": f"C{cid}", "facility_id": "BAD",
                       "provider_id": "DR-X", "patient_id": f"PT{cid % 8}",
                       "flagged": rng.random() < 0.35,
                       "tariff_z": rng.gauss(2.5, 0.8),
                       "duplicate_biometric": rng.random() < 0.2})
    return claims


class TestPipeline(unittest.TestCase):
    def test_build_graph_nodes_and_edges(self):
        g = pipeline.build_graph(_claims())
        self.assertIn("GOOD", g.node_ids)
        self.assertIn("BAD", g.node_ids)
        self.assertTrue(g.neighbours("BAD"))

    def test_ring_scores_rank_bad_facility_top(self):
        claims = _claims()
        labels = {"BAD": 1, "GOOD": 0}
        scores = pipeline.ring_scores(claims, labels)
        self.assertGreater(scores["BAD"], scores["GOOD"])
        self.assertGreater(scores["DR-X"], scores["DR-A"])

    def test_cold_start_label_propagation(self):
        scores = pipeline.ring_scores(_claims(), labelled_verdicts=None)
        self.assertGreater(scores["BAD"], scores["GOOD"])

    def test_assess_end_to_end(self):
        claims = _claims()
        out = pipeline.assess(claims, {"BAD": 1, "GOOD": 0},
                              draws=800, burn=400, seed=11)
        bad_fac = next(r for r in out["facilities"]["facilities"]
                       if r["facility"] == "BAD")
        good_fac = next(r for r in out["facilities"]["facilities"]
                        if r["facility"] == "GOOD")
        self.assertGreater(bad_fac["posterior_mean"], good_fac["posterior_mean"])
        # fused claim verdicts: bad-facility claims score higher on average
        def mean_p(fid):
            ps = [v["probability"] for k, v in out["claims"].items()
                  if any(c["id"] == k and c["facility_id"] == fid
                         for c in claims)]
            return sum(ps) / len(ps)
        self.assertGreater(mean_p("BAD"), mean_p("GOOD"))

    def test_facility_observations(self):
        obs = pipeline.facility_observations(_claims())
        self.assertEqual(obs["GOOD"]["claims"], 100)
        self.assertEqual(obs["BAD"]["claims"], 120)


if __name__ == "__main__":
    unittest.main()
