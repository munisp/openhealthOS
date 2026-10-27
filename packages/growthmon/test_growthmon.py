import unittest
from growthmon import (GrowthChart, GrowthError, classify_muac,
                       GREEN_MIN, RED_MAX)


class TestClassify(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(classify_muac(13.0)["band"], "green")
        self.assertEqual(classify_muac(12.0)["band"], "yellow")
        self.assertEqual(classify_muac(11.4)["band"], "red")

    def test_boundary_values(self):
        self.assertEqual(classify_muac(GREEN_MIN)["band"], "green")
        self.assertEqual(classify_muac(RED_MAX)["band"], "yellow")

    def test_oedema_overrides_good_muac(self):
        out = classify_muac(14.5, oedema=True)
        self.assertEqual(out["band"], "red")
        self.assertEqual(out["action"], "emergency-referral")
        self.assertIn("bilateral-pitting-oedema", out["evidence"])

    def test_age_out_of_range_refused(self):
        with self.assertRaises(GrowthError):
            classify_muac(13.0, age_months=3)
        with self.assertRaises(GrowthError):
            classify_muac(13.0, age_months=72)

    def test_implausible_and_missing_refused(self):
        with self.assertRaises(GrowthError):
            classify_muac(3.0)
        with self.assertRaises(GrowthError):
            classify_muac(None, oedema=False)


class TestChart(unittest.TestCase):
    def test_stall_needs_two_flat_visits(self):
        c = GrowthChart()
        r1 = c.record("c1", day=1, muac_cm=13.0, weight_kg=8.0)
        self.assertNotIn("review_flag", r1)
        r2 = c.record("c1", day=30, muac_cm=13.0, weight_kg=8.0)
        self.assertNotIn("review_flag", r2)  # one flat visit = noise
        r3 = c.record("c1", day=60, muac_cm=13.0, weight_kg=7.9)
        self.assertIn("review_flag", r3)

    def test_growth_resets_stall(self):
        c = GrowthChart()
        c.record("c1", day=1, muac_cm=13.0, weight_kg=8.0)
        c.record("c1", day=30, muac_cm=13.0, weight_kg=7.9)
        r = c.record("c1", day=60, muac_cm=13.0, weight_kg=8.4)
        self.assertNotIn("review_flag", r)

    def test_summary_latest_band_wins(self):
        c = GrowthChart()
        c.record("c1", day=1, muac_cm=11.0)
        c.record("c1", day=30, muac_cm=13.5)  # recovered
        c.record("c2", day=1, muac_cm=12.0)
        s = c.summary()
        self.assertEqual(s["latest_red"], 0)
        self.assertEqual(s["latest_yellow"], 1)
        self.assertEqual(s["children"], 2)

    def test_bad_weight_refused(self):
        c = GrowthChart()
        with self.assertRaises(GrowthError):
            c.record("c1", day=1, muac_cm=13.0, weight_kg=80.0)


if __name__ == "__main__":
    unittest.main()
