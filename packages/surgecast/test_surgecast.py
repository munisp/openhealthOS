
import unittest
from surgecast import project, staffing_need, SurgeError, MIN_HISTORY_DAYS


class TestSurgecast(unittest.TestCase):
    def test_insufficient_history_stated(self):
        out = project([5, 6], capacity=100, horizon_days=7)
        self.assertEqual(out["status"], "insufficient-history")
        self.assertEqual(out["need_days"], MIN_HISTORY_DAYS)

    def test_flat_projection_centers_on_baseline(self):
        hist = [10] * 14
        out = project(hist, capacity=100, horizon_days=5)
        self.assertEqual(out["baseline"], 10.0)
        self.assertEqual(out["days"][0]["mid"], 10.0)

    def test_band_widens_with_horizon(self):
        hist = [10] * 14
        out = project(hist, capacity=100, horizon_days=10)
        w1 = out["days"][0]["high"] - out["days"][0]["low"]
        w10 = out["days"][9]["high"] - out["days"][9]["low"]
        self.assertGreater(w10, w1)

    def test_growth_breach_names_the_day(self):
        hist = [10] * 14
        out = project(hist, capacity=12, horizon_days=30, growth=0.05,
                      resource="isolation-beds")
        self.assertIn("breach", out)
        self.assertEqual(out["breach"]["resource"], "isolation-beds")
        self.assertGreater(out["breach"]["day"], 0)

    def test_no_breach_no_breach_key(self):
        out = project([10] * 14, capacity=1000, horizon_days=5)
        self.assertNotIn("breach", out)

    def test_bad_capacity_refused(self):
        with self.assertRaises(SurgeError):
            project([10] * 14, capacity=0, horizon_days=5)

    def test_staffing_ratio_stated(self):
        s = staffing_need(23, ratio=5)
        self.assertEqual(s["staff_needed"], 5)  # ceil(23/5)
        self.assertEqual(s["ratio"], 5)


if __name__ == "__main__":
    unittest.main()
