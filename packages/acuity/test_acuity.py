"""test_acuity.py — absence is not a value; the oxygenation and
acid-base rules are gates; mortality is stratified, never flattened."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from acuity import (AcuityError, AcuityRegistry, apache2,  # noqa: E402
                    ACUITY_VERSION)

# a physiologically normal 40-year-old: the zero score
NORMAL = dict(temp_c=37.0, map_mmhg=90, hr=80, rr=14, fio2=0.21,
              pao2=95, ph=7.40, na=140, k=4.0, creat_mgdl=1.0,
              hct=40, wbc=8, gcs=15, age=40)


class TestScorer(unittest.TestCase):
    def test_normal_physiology_scores_zero(self):
        s = apache2(**NORMAL)
        self.assertEqual(s["aps"], 0)
        self.assertEqual(s["age_points"], 0)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["acuity_version"], ACUITY_VERSION)

    def test_missing_vital_refuses(self):
        kw = dict(NORMAL)
        kw["hr"] = None
        with self.assertRaises(AcuityError):
            apache2(**kw)

    def test_known_sick_profile(self):
        # 70y, septic: every variable deranged into a known band
        s = apache2(temp_c=39.5, map_mmhg=55, hr=125, rr=28,
                    fio2=0.4, pao2=58, ph=7.28, na=152, k=5.7,
                    creat_mgdl=2.2, arf=True, hct=26, wbc=17,
                    gcs=12, age=70,
                    chronic_health="nonsurgical-or-emergency")
        # temp 3, map 2, hr 2, rr 1, pao2 3, ph 2, na 1, k 1,
        # creat 3x2=6, hct 2, wbc 1, gcs 3  => aps 27; age 5; ch 5
        self.assertEqual(s["aps"], 27)
        self.assertEqual(s["age_points"], 5)
        self.assertEqual(s["chronic_health_points"], 5)
        self.assertEqual(s["total"], 37)

    def test_oxygenation_rule_is_a_gate(self):
        kw = dict(NORMAL, fio2=0.6)
        del kw["pao2"]
        with self.assertRaises(AcuityError):   # needs aado2 now
            apache2(**kw)
        s = apache2(**dict(kw, aado2=250))
        self.assertEqual(s["per_variable"]["oxygenation"], 2)
        self.assertEqual(s["per_variable"]["oxygenation_basis"],
                         "aado2")

    def test_acid_base_alternative_on_record(self):
        kw = dict(NORMAL)
        del kw["ph"]
        with self.assertRaises(AcuityError):   # neither ph nor hco3
            apache2(**kw)
        s = apache2(**dict(kw, hco3=24))
        self.assertEqual(s["per_variable"]["acid-base"], 0)
        self.assertEqual(s["per_variable"]["acid_base_basis"], "hco3")

    def test_arf_doubles_renal_points(self):
        s_no = apache2(**dict(NORMAL, creat_mgdl=2.2))
        s_arf = apache2(**dict(NORMAL, creat_mgdl=2.2, arf=True))
        self.assertEqual(s_no["per_variable"]["creatinine"], 3)
        self.assertEqual(s_arf["per_variable"]["creatinine"], 6)


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.reg = AcuityRegistry()
        self.reg.record_score("icu-1", "p-low", day=0,
                              score=apache2(**NORMAL))
        sick = apache2(**dict(NORMAL, temp_c=39.5, map_mmhg=55,
                              hr=125, rr=28, pao2=58, ph=7.28,
                              na=152, k=5.7, creat_mgdl=2.2, arf=True,
                              hct=26, wbc=17, gcs=12, age=70,
                              chronic_health="nonsurgical-or-"
                              "emergency"))
        self.reg.record_score("icu-1", "p-high-1", day=0, score=sick)
        self.reg.record_score("icu-1", "p-high-2", day=0, score=sick)

    def test_bare_integer_score_refuses(self):
        with self.assertRaises(AcuityError):
            self.reg.record_score("icu-1", "p-x", day=0, score=36)

    def test_duplicate_score_and_double_outcome_refuse(self):
        with self.assertRaises(AcuityError):
            self.reg.record_score("icu-1", "p-low", day=1,
                                  score=apache2(**NORMAL))
        self.reg.record_outcome("icu-1", "p-low", day=3,
                                outcome="survived")
        with self.assertRaises(AcuityError):
            self.reg.record_outcome("icu-1", "p-low", day=4,
                                    outcome="died")

    def test_mortality_stratified_with_denominators(self):
        self.reg.record_outcome("icu-1", "p-low", day=3,
                                outcome="survived")
        self.reg.record_outcome("icu-1", "p-high-1", day=2,
                                outcome="died")
        self.reg.record_outcome("icu-1", "p-high-2", day=2,
                                outcome="transferred")
        rep = self.reg.mortality_by_band("icu-1")
        self.assertEqual(rep["scored"], 3)
        low, mid, high, vhigh = rep["bands"]
        self.assertEqual(low["n"], 1)
        self.assertEqual(low["mortality_rate"], 0.0)
        self.assertEqual(vhigh["n"], 2)            # score 37 -> 30+
        self.assertEqual(vhigh["died"], 1)
        # transferred out: excluded from the rate, but not hidden
        self.assertEqual(vhigh["transferred_out"], 1)
        self.assertEqual(vhigh["closed_outcomes"], 1)
        self.assertEqual(vhigh["mortality_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
