"""Tests for packages/ophthal — ophthalmology module."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ophthalmology import EyeExam, Error, snellen_to_logmar  # noqa: E402


class TestEyeExam(unittest.TestCase):
    def setUp(self):
        self.exam = EyeExam("EYE-1", "Pax-1", by="dr-eye", day=1)

    def test_acuity_recorded(self):
        self.exam.record_acuity("OD", "snellen-m", 6, 6)
        self.exam.record_acuity("OS", "snellen-m", 6, 60)
        self.assertEqual(self.exam.acuity["OS"]["denominator"], 60)

    def test_laterality_enforced(self):
        with self.assertRaises(Error):
            self.exam.record_acuity("left", "snellen-m", 6, 6)

    def test_iop_plausible_bounds(self):
        self.exam.record_iop("OD", 16.0)
        with self.assertRaises(Error):
            self.exam.record_iop("OS", 95.0)

    def test_refraction_bounds(self):
        self.exam.record_refraction("OD", sphere=-2.5, cylinder=-0.75,
                                    axis=90)
        with self.assertRaises(Error):
            self.exam.record_refraction("OS", sphere=-45.0, cylinder=0,
                                        axis=0)
        with self.assertRaises(Error):
            self.exam.record_refraction("OS", sphere=0, cylinder=0,
                                        axis=200)

    def test_findings_registered(self):
        self.exam.record_finding("OD", "cataract")
        with self.assertRaises(Error):
            self.exam.record_finding("OS", "blob")

    def test_snapshot_checksummed(self):
        self.exam.record_acuity("OU", "snellen-m", 6, 6)
        out = self.exam.as_dict()
        self.assertEqual(len(out["sha256"]), 64)

    def test_logmar_conversion(self):
        self.assertEqual(snellen_to_logmar(6, 6), 0.0)
        self.assertEqual(snellen_to_logmar(6, 60), 1.0)
        with self.assertRaises(Error):
            snellen_to_logmar(0, 6)


if __name__ == "__main__":
    unittest.main()
