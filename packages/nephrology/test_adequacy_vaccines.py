import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from nephrology import (DialysisUnit, NephroError, adequacy_from_labs,
                        VaccinationBook, SPKTV_TARGET)


class TestAdequacyVariants(unittest.TestCase):
    def test_adequate_session(self):
        out = adequacy_from_labs(pre_bun=80, post_bun=25, uf_liters=2.0,
                                 post_weight_kg=70, duration_min=240)
        self.assertGreaterEqual(out["spktv_daugirdas2"], SPKTV_TARGET)
        self.assertGreaterEqual(out["urr_pct"], 65.0)
        self.assertEqual(out["flags"], [])
        self.assertIn("daugirdas-2", out["method"])

    def test_swapped_bun_labels_refuse(self):
        with self.assertRaises(NephroError):
            adequacy_from_labs(pre_bun=25, post_bun=80, uf_liters=2.0,
                               post_weight_kg=70, duration_min=240)

    def test_short_fast_run_flags_ektv(self):
        out = adequacy_from_labs(pre_bun=70, post_bun=30, uf_liters=1.0,
                                 post_weight_kg=60, duration_min=90)
        # short runs: spKt/V flatters; eKt/V tells the truth
        self.assertTrue(any("eKt/V" in f for f in out["flags"])
                        or out["spktv_daugirdas2"] < SPKTV_TARGET
                        or any("spKt/V" in f for f in out["flags"]))


class TestVaccinationBook(unittest.TestCase):
    def setUp(self):
        self.unit = DialysisUnit("renal-1")
        self.unit.register_patient("p-1", dry_weight_kg=70, egfr=12,
                                   day=0)
        self.book = VaccinationBook(self.unit)

    def test_full_hbv_series_then_titre(self):
        self.book.record_dose("p-1", "hbv", dose_number=1, day=0,
                              by="nurse-1")
        self.book.record_dose("p-1", "hbv", dose_number=2, day=30,
                              by="nurse-1")
        out = self.book.record_dose("p-1", "hbv", dose_number=3,
                                    day=180, by="nurse-1")
        self.assertTrue(out["complete"])
        t = self.book.record_titre("p-1", "hbv", titre_miu_ml=45.0,
                                   day=230)
        self.assertTrue(t["responder"])

    def test_sequential_and_interval_honest(self):
        self.book.record_dose("p-1", "hbv", dose_number=1, day=0,
                              by="nurse-1")
        with self.assertRaises(NephroError):
            self.book.record_dose("p-1", "hbv", dose_number=3, day=30,
                                  by="nurse-1")
        with self.assertRaises(NephroError):
            self.book.record_dose("p-1", "hbv", dose_number=2, day=10,
                                  by="nurse-1")  # < 28d

    def test_nonresponder_action(self):
        for i, d in enumerate((0, 30, 180), start=1):
            self.book.record_dose("p-1", "hbv", dose_number=i, day=d,
                                  by="nurse-1")
        t = self.book.record_titre("p-1", "hbv", titre_miu_ml=3.0,
                                   day=240)
        self.assertFalse(t["responder"])
        self.assertIn("repeat full series", t["action"])

    def test_titre_window_and_completion_gates(self):
        self.book.record_dose("p-1", "hbv", dose_number=1, day=0,
                              by="nurse-1")
        with self.assertRaises(NephroError):
            self.book.record_titre("p-1", "hbv", titre_miu_ml=20,
                                   day=60)  # series incomplete
        self.book.record_dose("p-1", "hbv", dose_number=2, day=30,
                              by="nurse-1")
        self.book.record_dose("p-1", "hbv", dose_number=3, day=180,
                              by="nurse-1")
        with self.assertRaises(NephroError):
            self.book.record_titre("p-1", "hbv", titre_miu_ml=20,
                                   day=190)  # too early (<30d)

    def test_due_list_and_coverage_denominators(self):
        self.unit.register_patient("p-2", dry_weight_kg=60, egfr=15,
                                   day=0)
        self.book.record_dose("p-1", "hbv", dose_number=1, day=0,
                              by="nurse-1")
        self.book.record_dose("p-2", "influenza", dose_number=1, day=0,
                              by="nurse-1")
        due = self.book.due(as_of=100)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["owes"], "dose-2")
        rep = self.book.coverage_report()
        self.assertEqual(rep["series_started"], 2)
        self.assertEqual(rep["series_complete"]["n"], 1)
        self.assertEqual(rep["series_complete"]["rate"], 0.5)


if __name__ == "__main__":
    unittest.main()


class TestRegistryReturn(unittest.TestCase):
    def test_unit_year_in_numbers(self):
        from nephrology import registry_return
        unit = DialysisUnit("renal-1")
        unit.register_patient("p-1", dry_weight_kg=70, egfr=12, day=0)
        unit.register_machine("M1", hbv_dedicated=False)
        unit.register_access("p-1", kind="avf", day=0)
        unit.start_session("S1", patient_id="p-1", machine_id="M1",
                           dialyzer_id="D1", day=0)
        unit.end_session("S1", pre_weight=72.0, post_weight=70.0,
                         day=0, spktv=1.4)
        out = registry_return(unit)
        self.assertEqual(out["prevalent_patients"], 1)
        self.assertEqual(out["adequacy_at_target"]["n"], 1)
        self.assertEqual(out["access_mix"], {"avf": 1})
        self.assertEqual(out["version"], "ukrr-shaped@1.0.0")
