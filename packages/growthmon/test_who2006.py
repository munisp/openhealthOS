"""Tests for packages/growthmon/who2006 — WHO 2006 z-score engine."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from who2006 import (  # noqa: E402
    Anthropometry, LMSRegistry, Error, _lms_z, classify,
    TABLE_STATUS_STARTER, TABLE_STATUS_FULL, _STARTER,
)


class TestLMSMath(unittest.TestCase):
    def test_median_gives_zero(self):
        self.assertAlmostEqual(_lms_z(9.6479, -0.3521, 9.6479, 0.10125),
                               0.0, places=6)

    def test_logarithmic_special_case(self):
        import math
        self.assertAlmostEqual(_lms_z(10.0, 0.0, 8.0, 0.1),
                               math.log(10.0 / 8.0) / 0.1, places=9)

    def test_known_direction(self):
        # below the median -> negative z; above -> positive
        self.assertLess(_lms_z(7.0, -0.3521, 9.6479, 0.10125), 0)
        self.assertGreater(_lms_z(11.0, -0.3521, 9.6479, 0.10125), 0)


class TestClassify(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(classify("wfa", -3.5), "severe")
        self.assertEqual(classify("wfa", -2.5), "moderate")
        self.assertEqual(classify("wfa", -1.0), "normal")
        self.assertEqual(classify("wfh", 2.5), "overweight-risk")
        self.assertEqual(classify("wfa", 2.5), "normal")

    def test_impossible_z_refused(self):
        with self.assertRaises(Error):
            classify("wfa", -12.0)


class TestRegistry(unittest.TestCase):
    def test_starter_status_default(self):
        reg = LMSRegistry()
        self.assertEqual(reg.status, TABLE_STATUS_STARTER)

    def test_out_of_domain_refused(self):
        reg = LMSRegistry()
        with self.assertRaises(Error):
            reg.lms("wfa", "male", 70.0)   # beyond 60 months
        with self.assertRaises(Error):
            reg.lms("wfh", "female", 30.0)  # below 45 cm

    def test_interpolation_between_anchors(self):
        reg = LMSRegistry()
        l, m, s = reg.lms("wfa", "male", 6.0)
        m0 = _STARTER[("wfa", "male")][0][2]
        m1 = _STARTER[("wfa", "male")][1][2]
        self.assertTrue(m0 < m < m1)

    def test_load_full_table(self):
        reg = LMSRegistry()
        pts = [(float(i), -0.35, 3.0 + i * 0.3, 0.1) for i in range(61)]
        n = reg.load_lms_table("wfa", "male", pts, source="WHO 2006 official")
        self.assertEqual(n, 61)
        self.assertEqual(reg.status, TABLE_STATUS_FULL)

    def test_bad_table_refused(self):
        reg = LMSRegistry()
        with self.assertRaises(Error):
            reg.load_lms_table("wfa", "male", [(0, -0.3, 3.0, 0.1)],
                               source="x")                       # too few
        with self.assertRaises(Error):
            reg.load_lms_table("wfa", "male",
                               [(0, -0.3, 3.0, 0.1), (0, -0.3, 4.0, 0.1),
                                (2, -0.3, 3.6, 0.1), (3, -0.3, 3.9, 0.1)],
                               source="x")                       # not ascending
        with self.assertRaises(Error):
            reg.load_lms_table("wfa", "male",
                               [(i, -0.3, 3 + i, 0.1) for i in range(5)],
                               source="")                        # no source


class TestAnthropometry(unittest.TestCase):
    def setUp(self):
        self.anthro = Anthropometry()

    def test_underweight_boy(self):
        # 12-month-old boy at 7.0kg vs median 9.6479 -> clearly negative
        r = self.anthro.z_score("wfa", "male", x=12.0, value=7.0)
        self.assertLess(r["z"], -2.0)
        self.assertIn(r["band"], ("moderate", "severe"))
        self.assertEqual(r["table_status"], TABLE_STATUS_STARTER)

    def test_healthy_girl(self):
        r = self.anthro.z_score("wfa", "female", x=12.0, value=8.9481)
        self.assertAlmostEqual(r["z"], 0.0, places=1)
        self.assertEqual(r["band"], "normal")

    def test_stunting_via_hfa(self):
        # 24-month-old boy at 76cm vs median ~87 -> stunted
        r = self.anthro.z_score("hfa", "male", x=24.0, value=76.0)
        self.assertLess(r["z"], -2.0)

    def test_implausible_value_refused(self):
        with self.assertRaises(Error):
            self.anthro.z_score("wfa", "male", x=6.0, value=40.0)

    def test_unknown_measure_or_sex_refused(self):
        with self.assertRaises(Error):
            self.anthro.z_score("bmi", "male", x=12.0, value=16.0)
        with self.assertRaises(Error):
            self.anthro.z_score("wfa", "other", x=12.0, value=9.0)

    def test_grade_child_states_absence(self):
        out = self.anthro.grade_child("female", age_months=18.0,
                                      weight_kg=10.0)
        self.assertIsNone(out["hfa"])   # stated, not skipped
        self.assertIsNone(out["wfh"])
        self.assertEqual(len(out["sha256"]), 64)
        full = self.anthro.grade_child("female", age_months=18.0,
                                       weight_kg=10.0, height_cm=80.0)
        self.assertIsNotNone(full["hfa"])
        self.assertIsNotNone(full["wfh"])

    def test_full_table_status_travels(self):
        reg = LMSRegistry()
        for sex in ("male", "female"):
            for meas in ("wfa", "hfa", "wfh"):
                dom = {"wfa": 61, "hfa": 61, "wfh": 76}[meas]
                base = {"wfa": (0.0, 3.0), "hfa": (0.0, 49.0),
                        "wfh": (45.0, 2.4)}[meas]
                pts = [(base[0] + i, -0.35, base[1] + i * 0.3, 0.1)
                       for i in range(dom)]
                reg.load_lms_table(meas, sex, pts, source="WHO 2006")
        a = Anthropometry(reg)
        r = a.z_score("wfa", "male", x=12.0, value=9.0)
        self.assertEqual(r["table_status"], TABLE_STATUS_FULL)


if __name__ == "__main__":
    unittest.main()
