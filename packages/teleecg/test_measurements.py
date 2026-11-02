import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "teleread"))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from teleecg import (ECGService, compute_qtc, MEASUREMENT_VERSION)


def svc():
    r_set = __import__("teleread").ReaderRegistry()
    r_set.register("card-1", licence_id="MDCN-C1",
                   licence_expiry_day=400, scopes=("cardiac-reader",))
    s = ECGService(r_set)
    s.record_tracing("T1", patient_id="p1", kind="12-lead",
                     device_id=None, waveform_ref="w1",
                     facility="phc-1", referrer="n-1", day=1)
    s.record_tracing("T2", patient_id="p2", kind="single-lead",
                     device_id=None, waveform_ref="w2",
                     facility="phc-1", referrer="n-1", day=1)
    return s


class TestQTcComputation(unittest.TestCase):
    def test_methods_named_and_distinct(self):
        qt, rr = 400, 1000  # 60 bpm: all methods agree at RR=1s
        self.assertEqual(compute_qtc(qt, rr, method="bazett"), 400)
        self.assertEqual(compute_qtc(qt, rr, method="fridericia"), 400)
        self.assertEqual(compute_qtc(qt, rr, method="framingham"), 400)
        # at a fast rate they diverge — which is exactly why the
        # method is mandatory
        rr = 600  # 100 bpm
        b = compute_qtc(360, rr, method="bazett")
        f = compute_qtc(360, rr, method="fridericia")
        self.assertNotEqual(b, f)
        self.assertGreater(b, 400)  # Bazett overcorrects at high rates

    def test_unknown_method_refused(self):
        with self.assertRaises(TeleReadError):
            compute_qtc(400, 1000, method="hodges-ish")


class TestRecordMeasurements(unittest.TestCase):
    def test_clean_read_has_no_flags(self):
        s = svc()
        m = s.record_measurements("T1", by="tech-1", day=1,
                                  rate_bpm=72, pr_ms=160, qrs_ms=90,
                                  qt_ms=380, rr_ms=833,
                                  qtc_method="fridericia",
                                  axis_deg=60, patient_sex="male")
        self.assertEqual(m["flags"], [])
        self.assertEqual(m["version"], MEASUREMENT_VERSION)
        self.assertEqual(m["qtc_method"], "fridericia")

    def test_implausible_values_refuse_loudly(self):
        s = svc()
        with self.assertRaises(TeleReadError):
            s.record_measurements("T1", by="tech-1", day=1,
                                  rate_bpm=8, pr_ms=160, qrs_ms=90,
                                  qt_ms=380, rr_ms=833,
                                  qtc_method="bazett")
        # and nothing was filed
        with self.assertRaises(TeleReadError):
            s.intervals("T1")

    def test_qtc_required_one_way_or_another(self):
        s = svc()
        with self.assertRaises(TeleReadError):
            s.record_measurements("T1", by="tech-1", day=1,
                                  rate_bpm=72, pr_ms=160, qrs_ms=90)
        with self.assertRaises(TeleReadError):
            # supplied QTc without a named method
            s.record_measurements("T1", by="tech-1", day=1,
                                  rate_bpm=72, pr_ms=160, qrs_ms=90,
                                  qtc_ms=410)

    def test_danger_zone_and_block_flags(self):
        s = svc()
        m = s.record_measurements("T1", by="tech-1", day=1,
                                  rate_bpm=55, pr_ms=220, qrs_ms=130,
                                  qt_ms=530, rr_ms=1091,
                                  qtc_method="bazett", axis_deg=-45)
        blob = " ".join(m["flags"])
        self.assertIn("danger", blob)
        self.assertIn("first-degree", blob)
        self.assertIn("conduction delay", blob)
        self.assertIn("left axis", blob)

    def test_unknown_sex_reports_between_bounds(self):
        s = svc()
        m = s.record_measurements("T1", by="tech-1", day=1,
                                  rate_bpm=70, pr_ms=160, qrs_ms=90,
                                  qtc_ms=460, qtc_method="bazett")
        self.assertIn("record patient sex to resolve",
                      " ".join(m["flags"]))

    def test_axis_needs_limb_leads(self):
        s = svc()
        with self.assertRaises(TeleReadError):
            s.record_measurements("T2", by="tech-1", day=1,
                                  rate_bpm=70, pr_ms=160, qrs_ms=90,
                                  qtc_ms=410, qtc_method="bazett",
                                  axis_deg=60)

    def test_corrections_are_append_only(self):
        s = svc()
        s.record_measurements("T1", by="tech-1", day=1,
                              rate_bpm=72, pr_ms=160, qrs_ms=90,
                              qtc_ms=410, qtc_method="bazett")
        s.record_measurements("T1", by="card-1", day=2,
                              rate_bpm=74, pr_ms=158, qrs_ms=92,
                              qtc_ms=405, qtc_method="fridericia")
        iv = s.intervals("T1")
        self.assertEqual(len(iv["history"]), 2)
        self.assertEqual(iv["history"][0]["by"], "tech-1")  # kept
        self.assertEqual(iv["latest"]["by"], "card-1")
        self.assertEqual(iv["latest"]["v"], 2)


if __name__ == "__main__":
    unittest.main()
