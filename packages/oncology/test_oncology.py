"""Tests for packages/oncology — chemotherapy regimens and cycles."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from oncology import (  # noqa: E402
    OncologyCourse, Error, register_regimen, bsa_mosteller, REGIMENS,
)


def setup_regimen():
    if "CHOP" not in REGIMENS:
        register_regimen(
            "CHOP",
            drugs=[
                {"name": "cyclophosphamide", "mg_per_m2": 750,
                 "absolute_max_mg": 1500},
                {"name": "doxorubicin", "mg_per_m2": 50,
                 "absolute_max_mg": 100},
                {"name": "vincristine", "mg_per_m2": 1.4,
                 "absolute_max_mg": 2.0},
            ],
            cycle_length_days=21, max_cycles=6, ratified_by="dr-onc")


def good_bloods():
    return {"neutrophils": 2.5, "platelets": 150.0, "haemoglobin": 11.0}


class TestRegimen(unittest.TestCase):
    def test_register_and_reject_duplicate(self):
        setup_regimen()
        with self.assertRaises(Error):
            register_regimen("CHOP", drugs=[], cycle_length_days=21,
                             max_cycles=6, ratified_by="x")
        with self.assertRaises(Error):
            register_regimen("FAKE", drugs=[], cycle_length_days=21,
                             max_cycles=6, ratified_by="dr-onc")

    def test_bsa_mosteller(self):
        # 170cm, 70kg -> sqrt(11900/3600) = ~1.818
        bsa = bsa_mosteller(170.0, 70.0)
        self.assertAlmostEqual(bsa, 1.818, places=2)
        with self.assertRaises(Error):
            bsa_mosteller(300.0, 70.0)


class TestCycleGate(unittest.TestCase):
    def setUp(self):
        setup_regimen()
        self.course = OncologyCourse("OC-1", "Pax-1", "CHOP",
                                     oncologist="dr-onc",
                                     diagnosis="DLBCL")

    def test_neutropenia_refuses_cycle(self):
        with self.assertRaises(Error):
            self.course.plan_cycle(
                1, height_cm=170, weight_kg=70, day=1,
                bloods={"neutrophils": 0.6, "platelets": 150,
                        "haemoglobin": 11.0})

    def test_thrombocytopenia_refuses_cycle(self):
        with self.assertRaises(Error):
            self.course.plan_cycle(
                1, height_cm=170, weight_kg=70, day=1,
                bloods={"neutrophils": 2.0, "platelets": 30,
                        "haemoglobin": 11.0})

    def test_missing_bloods_refused(self):
        with self.assertRaises(Error):
            self.course.plan_cycle(
                1, height_cm=170, weight_kg=70, day=1,
                bloods={"neutrophils": 2.0, "platelets": 150})

    def test_grade3_toxicity_gates_next(self):
        c1 = self.course.plan_cycle(1, height_cm=170, weight_kg=70, day=1,
                                    bloods=good_bloods())
        c1.record_administration(by="nurse-chemo",
                                 nurse_witness="nurse-2")
        c1.record_toxicity(3, assessed_by="dr-onc", day=10)
        with self.assertRaises(Error):
            self.course.plan_cycle(2, height_cm=170, weight_kg=70, day=22,
                                   bloods=good_bloods(),
                                   previous_toxicity_grade=3)

    def test_bsa_dose_capping(self):
        c1 = self.course.plan_cycle(1, height_cm=170, weight_kg=70, day=1,
                                    bloods=good_bloods())
        vcr = [d for d in c1.doses if d["drug"] == "vincristine"][0]
        self.assertTrue(vcr["capped"])
        self.assertEqual(vcr["administered_mg"], 2.0)
        cyclo = [d for d in c1.doses
                 if d["drug"] == "cyclophosphamide"][0]
        self.assertFalse(cyclo["capped"])

    def test_administration_witnessed(self):
        c1 = self.course.plan_cycle(1, height_cm=170, weight_kg=70, day=1,
                                    bloods=good_bloods())
        with self.assertRaises(Error):
            c1.record_administration(by="nurse-chemo", nurse_witness=None)
        c1.record_administration(by="nurse-chemo", nurse_witness="nurse-2")
        self.assertTrue(c1.administered)

    def test_cycles_sequential(self):
        self.course.plan_cycle(1, height_cm=170, weight_kg=70, day=1,
                               bloods=good_bloods())
        with self.assertRaises(Error):
            self.course.plan_cycle(1, height_cm=170, weight_kg=70, day=22,
                                   bloods=good_bloods())

    def test_course_summary(self):
        self.course.plan_cycle(1, height_cm=170, weight_kg=70, day=1,
                               bloods=good_bloods())
        s = self.course.course_summary()
        self.assertEqual(s["cycles_planned"], 1)
        self.assertEqual(len(s["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
