import os
import sys
import unittest

for pkg in ("teleread",):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from telederm import TeleDerm


class TestTeleDerm(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("derm-1", licence_id="D1", licence_expiry_day=400,
                   scopes=("derm-reader",))
        self.svc = TeleDerm(r)

    def test_consent_structural(self):
        with self.assertRaises(TeleReadError):
            self.svc.submit_lesion("L1", patient_id="p1",
                                   body_site="forearm", image_ref="i1",
                                   consent="implied", facility="f",
                                   referrer="n1", day=0, duration_days=30)

    def test_ntd_flags_fail_closed_and_escalate_priority(self):
        with self.assertRaises(TeleReadError):
            self.svc.submit_lesion("L1", patient_id="p1",
                                   body_site="forearm", image_ref="i1",
                                   consent="signed", facility="f",
                                   referrer="n1", day=0, duration_days=30,
                                   ntd_flags=("alien",))
        out = self.svc.submit_lesion("L1", patient_id="p1",
                                     body_site="forearm", image_ref="i1",
                                     consent="verbal-witnessed",
                                     facility="f", referrer="n1", day=0,
                                     duration_days=365,
                                     ntd_flags=("leprosy-suspect",))
        self.assertEqual(out["priority"], "urgent")

    def test_surveillance_aggregate_only(self):
        self.svc.submit_lesion("L1", patient_id="p1", body_site="hand",
                               image_ref="i1", consent="signed",
                               facility="f", referrer="n1", day=0,
                               duration_days=60,
                               ntd_flags=("leprosy-suspect",))
        agg = self.svc.ntd_surveillance()
        self.assertEqual(agg["counts"]["leprosy-suspect"], 1)
        self.assertNotIn("patient_id", str(agg["counts"]))


if __name__ == "__main__":
    unittest.main()
