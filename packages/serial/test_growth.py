import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from serial import (SerialError, growth_assessment, growth_z,  # noqa: E402
                    GROWTH_REF_VERSION, BIOMETRY_TABLE)


class TestGrowthCharting(unittest.TestCase):
    def test_z_score_midline_is_zero(self):
        ga = 30
        i = ("bpd", "hc", "ac", "fl").index("ac")
        p5, p95 = BIOMETRY_TABLE[ga][i]
        mid = (p5 + p95) / 2
        z = growth_z("ac", ga, mid)
        self.assertAlmostEqual(z, 0.0, places=1)

    def test_outside_table_abstains_not_zero(self):
        self.assertIsNone(growth_z("ac", 50, 300))
        self.assertIsNone(growth_z("ac", 10, 60))

    def test_unknown_param_refuses(self):
        with self.assertRaises(SerialError):
            growth_z("femur-width", 30, 50)

    def test_single_point_refuses(self):
        with self.assertRaises(SerialError) as ctx:
            growth_assessment([(30, {"ac": 260})])
        self.assertIn("trajectory", str(ctx.exception))

    def test_centile_crossing_is_the_finding(self):
        # ac plunges across bands between 30w and 34w
        series = [(30, {"ac": 262}), (34, {"ac": 272})]
        out = growth_assessment(series)
        self.assertEqual(out["ref_version"], GROWTH_REF_VERSION)
        self.assertTrue(any("crossed centile bands" in f
                            and "faltering" in f for f in out["flags"]))
        self.assertEqual(len(out["points"]), 2)

    def test_steady_growth_is_quiet(self):
        series = [(26, {"ac": 220}), (30, {"ac": 262}),
                  (34, {"ac": 295})]
        out = growth_assessment(series)
        self.assertEqual(out["flags"], [])


if __name__ == "__main__":
    unittest.main()
