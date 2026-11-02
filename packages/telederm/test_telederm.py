import os
import sys
import unittest

for pkg in ("teleread", "duelgrade"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from telederm import TeleDerm, DERM_CHECKS


class TestTeleDerm(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("derm-1", licence_id="D1", licence_expiry_day=400,
                   scopes=("derm-reader",))
        self.svc = TeleDerm(r)

    def _good_capture(self, ref, day=0):
        self.svc.assess_capture(ref, day=day, by="nurse-1",
                                passed=DERM_CHECKS)

    def test_consent_structural(self):
        with self.assertRaises(TeleReadError):
            self.svc.submit_lesion("L1", patient_id="p1",
                                   body_site="forearm", image_ref="i1",
                                   consent="implied", facility="f",
                                   referrer="n1", day=0, duration_days=30)

    def test_capture_qa_gate(self):
        # never assessed — refuses
        with self.assertRaises(TeleReadError):
            self.svc.submit_lesion("L1", patient_id="p1",
                                   body_site="forearm", image_ref="i1",
                                   consent="signed", facility="f",
                                   referrer="n1", day=0, duration_days=30)
        # ungradable: no scale marker, lesion cropped — failed consult
        self.svc.assess_capture("i1", day=0, by="nurse-1",
                                passed=("focus",),
                                failed=("scale-marker",
                                        "whole-lesion-in-frame",
                                        "dermoscopic-or-clinical-view"))
        with self.assertRaises(TeleReadError):
            self.svc.submit_lesion("L1", patient_id="p1",
                                   body_site="forearm", image_ref="i1",
                                   consent="signed", facility="f",
                                   referrer="n1", day=0, duration_days=30)
        # retake on a new image proceeds
        self._good_capture("i1b")
        out = self.svc.submit_lesion("L1", patient_id="p1",
                                     body_site="forearm", image_ref="i1b",
                                     consent="signed", facility="f",
                                     referrer="n1", day=0,
                                     duration_days=30)
        self.assertEqual(out["case_id"], "L1")

    def test_ntd_flags_fail_closed_and_escalate_priority(self):
        with self.assertRaises(TeleReadError):
            self.svc.submit_lesion("L1", patient_id="p1",
                                   body_site="forearm", image_ref="i1",
                                   consent="signed", facility="f",
                                   referrer="n1", day=0, duration_days=30,
                                   ntd_flags=("alien",))
        self._good_capture("i1")
        out = self.svc.submit_lesion("L1", patient_id="p1",
                                     body_site="forearm", image_ref="i1",
                                     consent="verbal-witnessed",
                                     facility="f", referrer="n1", day=0,
                                     duration_days=365,
                                     ntd_flags=("leprosy-suspect",))
        self.assertEqual(out["priority"], "urgent")

    def test_surveillance_aggregate_only(self):
        self._good_capture("i1")
        self.svc.submit_lesion("L1", patient_id="p1", body_site="hand",
                               image_ref="i1", consent="signed",
                               facility="f", referrer="n1", day=0,
                               duration_days=60,
                               ntd_flags=("leprosy-suspect",))
        agg = self.svc.ntd_surveillance()
        self.assertEqual(agg["counts"]["leprosy-suspect"], 1)
        self.assertNotIn("patient_id", str(agg["counts"]))


class TestTwoWeekWait(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("derm-1", licence_id="D1", licence_expiry_day=400,
                   scopes=("derm-reader",))
        self.svc = TeleDerm(r)
        self.svc.assess_capture("i1", day=0, by="nurse-1",
                                passed=DERM_CHECKS)
        self.svc.submit_lesion("L1", patient_id="p1",
                               body_site="shoulder", image_ref="i1",
                               consent="signed", facility="f",
                               referrer="n1", day=0, duration_days=120)

    def test_clock_starts_on_named_suspicion(self):
        with self.assertRaises(TeleReadError):  # unknown case
            self.svc.raise_suspicion("ghost", finding="suspected-scc",
                                     day=0, by="derm-1")
        with self.assertRaises(TeleReadError):  # not a cancer finding
            self.svc.raise_suspicion("L1", finding="scabies", day=0,
                                     by="derm-1")
        with self.assertRaises(TeleReadError):  # unsigned
            self.svc.raise_suspicion("L1", finding="suspected-melanoma",
                                     day=0, by="")
        p = self.svc.raise_suspicion("L1", finding="suspected-melanoma",
                                     day=0, by="derm-1")
        self.assertEqual(p["target_day"], 14)
        with self.assertRaises(TeleReadError):  # clock does not restart
            self.svc.raise_suspicion("L1", finding="suspected-melanoma",
                                     day=3, by="derm-1")

    def test_pathway_is_sequential_and_evidenced(self):
        self.svc.raise_suspicion("L1", finding="suspected-scc", day=0,
                                 by="derm-1")
        with self.assertRaises(TeleReadError):  # skip to attended
            self.svc.advance_pathway("L1", "attended", day=5)
        with self.assertRaises(TeleReadError):  # referral needs a ref
            self.svc.advance_pathway("L1", "referred", day=1)
        self.svc.advance_pathway("L1", "referred", day=1,
                                 ref="REF-LAG-77")
        self.svc.advance_pathway("L1", "attended", day=10)
        with self.assertRaises(TeleReadError):  # diagnosis needs an answer
            self.svc.advance_pathway("L1", "diagnosed", day=12)
        with self.assertRaises(TeleReadError):  # ... and a method
            self.svc.advance_pathway("L1", "diagnosed", day=12,
                                     diagnosis="scc", confirmed_by="vibes")
        p = self.svc.advance_pathway("L1", "diagnosed", day=12,
                                     diagnosis="scc",
                                     confirmed_by="histology")
        self.assertEqual(p["confirmed_by"], "histology")

    def test_breaches_overdue_first_and_report_denominators(self):
        self.svc.raise_suspicion("L1", finding="suspected-melanoma",
                                 day=0, by="derm-1")
        breaches = self.svc.two_week_breaches(as_of=13)
        self.assertEqual(breaches, [])          # not yet past target
        breaches = self.svc.two_week_breaches(as_of=20)
        self.assertEqual(breaches[0]["overdue_days"], 6)
        self.assertEqual(breaches[0]["stage"], "suspected")
        # referred-but-unseen is still a breach
        self.svc.advance_pathway("L1", "referred", day=2, ref="REF-1")
        b = self.svc.two_week_breaches(as_of=20)
        self.assertEqual(b[0]["stage"], "referred")
        # attendance inside target clears it
        self.svc.advance_pathway("L1", "attended", day=12)
        self.assertEqual(self.svc.two_week_breaches(as_of=20), [])
        rep = self.svc.pathway_report(as_of=20)
        self.assertEqual(rep["suspected"], 1)
        self.assertEqual(rep["attended_within_14d"], 1)
        self.assertEqual(rep["within_target_rate"], 1.0)
        self.assertEqual(rep["median_days_to_attend"], 12)
        self.assertEqual(rep["breaches_now"], 0)


if __name__ == "__main__":
    unittest.main()
