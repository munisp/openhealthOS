
import unittest
from coldwitness import (StabilityRegistry, degree_hours, adjudicate,
                         ColdError, DEFAULT_STABILITY)


class TestColdWitness(unittest.TestCase):
    def test_cold_series_is_usable(self):
        r = [(h, 4.0) for h in range(48)]
        out = adjudicate("bcg", r)
        self.assertEqual(out["verdict"], "usable")
        self.assertEqual(out["degree_hours"], 0.0)

    def test_long_excursion_discards(self):
        # 30h at 20°C: 30 * (20-8) = 360 degree-hours >> bcg budget 24
        r = [(h, 4.0) for h in range(10)] + [(h, 20.0) for h in range(10, 40)]
        out = adjudicate("bcg", r)
        self.assertEqual(out["verdict"], "discard")
        self.assertGreater(out["budget_consumed"], 1.0)

    def test_partial_excursion_quarantines(self):
        # consume ~60% of bcg budget: 24 * 0.6 = 14.4 dh via 18°C plateau
        # segments: avg 11 excess 3 *0.9=2.7 + avg 18 excess 10 *0.9=9.0
        # + avg 11 excess 3 *0.9=2.7 → 14.4 dh > 12 (50% of 24)
        r = [(0.0, 4.0), (0.9, 18.0), (1.8, 18.0), (2.7, 4.0)]
        out = adjudicate("bcg", r)
        self.assertEqual(out["verdict"], "quarantine")

    def test_logger_gap_holds_stock(self):
        r = [(0.0, 4.0), (6.0, 4.0)]  # 5-hour gap
        out = adjudicate("bcg", r)
        self.assertEqual(out["verdict"], "quarantine")
        self.assertEqual(len(out["logger_gaps"]), 1)

    def test_unknown_antigen_refused(self):
        with self.assertRaises(ColdError):
            adjudicate("smallpox-horse-serum", [(0, 4.0), (1, 4.0)])

    def test_single_reading_refused(self):
        with self.assertRaises(ColdError):
            degree_hours([(0.0, 4.0)], threshold_c=8.0)

    def test_unordered_readings_refused(self):
        with self.assertRaises(ColdError):
            degree_hours([(2.0, 4.0), (1.0, 4.0)], threshold_c=8.0)

    def test_mrna_freezer_threshold(self):
        # -20°C is FINE for mrna (threshold -15) but excursion for +8 antigens
        r = [(h, -20.0) for h in range(10)]
        self.assertEqual(adjudicate("covid-mrna", r)["verdict"], "usable")
        self.assertEqual(adjudicate("bcg", r)["verdict"], "usable")  # cold ≠ warm

    def test_registry_ratify(self):
        reg = StabilityRegistry()
        reg.ratify("new-vax", threshold_c=8.0, budget_degree_hours=6.0,
                   by="pharmacist-in-charge")
        self.assertEqual(reg.spec("new-vax")["budget_degree_hours"], 6.0)


if __name__ == "__main__":
    unittest.main()
