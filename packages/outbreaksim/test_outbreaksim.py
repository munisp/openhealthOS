import unittest
from outbreaksim import simulate, compare_strategies, SimError


KW = dict(seed=42, generations=12, r0=2.5, population=10_000,
          detection=0.5)


class TestOutbreakSim(unittest.TestCase):
    def test_reproducible_byte_for_byte(self):
        a = simulate(**KW)
        b = simulate(**KW)
        self.assertEqual(a["checksum"], b["checksum"])
        self.assertEqual(a["log"], b["log"])

    def test_different_seed_differs(self):
        a = simulate(**KW)
        b = simulate(**{**KW, "seed": 43})
        self.assertNotEqual(a["checksum"], b["checksum"])

    def test_ring_vaccination_averts_cases(self):
        out = compare_strategies(**KW, vaccinate_from_gen=3)
        self.assertGreaterEqual(out["cases_averted"], 0)
        self.assertEqual(out["baseline"]["seed"],
                         out["ring_vaccination"]["seed"])

    def test_zero_r0_dies_out(self):
        out = simulate(**{**KW, "r0": 0.0})
        self.assertEqual(out["total_cases"], 1)  # index case only

    def test_detection_respected(self):
        hi = simulate(**{**KW, "detection": 0.9})
        lo = simulate(**{**KW, "detection": 0.1})
        self.assertGreater(hi["detected"], lo["detected"])

    def test_history_append_only(self):
        out = compare_strategies(**KW, vaccinate_from_gen=3)
        base_gens = out["baseline"]["log"]
        vacc_gens = out["ring_vaccination"]["log"]
        # generations before vaccination are identical
        for g in range(3):
            self.assertEqual(base_gens[g], vacc_gens[g])

    def test_population_cap_holds(self):
        out = simulate(seed=7, generations=30, r0=5.0, population=200,
                       detection=0.5)
        self.assertLessEqual(out["total_cases"], 200)

    def test_bad_probabilities_refused(self):
        with self.assertRaises(SimError):
            simulate(**{**KW, "detection": 1.5})

    def test_tiny_population_refused(self):
        with self.assertRaises(SimError):
            simulate(**{**KW, "population": 3})


if __name__ == "__main__":
    unittest.main()
