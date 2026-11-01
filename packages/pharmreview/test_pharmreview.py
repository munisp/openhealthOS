import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "teleread"))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from pharmreview import PharmacyReview


class TestPharmReview(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("pharm-1", licence_id="PCN-1", licence_expiry_day=400,
                   scopes=("clinical-pharmacist",))
        self.svc = PharmacyReview(r)
        self.svc.submit_order("O1", patient_id="p1", drug="warfarin",
                              dose=5, route="po", risk_class="anticoagulant",
                              prescriber="mo-1", facility="phc-1", day=0)

    def test_routine_orders_dont_queue(self):
        with self.assertRaises(TeleReadError):
            self.svc.submit_order("O2", patient_id="p2", drug="pcm",
                                  dose=500, route="po",
                                  risk_class="analgesic", prescriber="mo-1",
                                  facility="phc-1", day=0)

    def test_override_requires_reason(self):
        self.svc.intervene("O1", pharmacist="pharm-1", day=0,
                           recommendation="hold; INR 4.2",
                           reason="supratherapeutic")
        with self.assertRaises(TeleReadError):
            self.svc.prescriber_responds("O1", day=0, accept=False)
        out = self.svc.prescriber_responds("O1", day=0, accept=False,
                                           override_reason="bleeding "
                                           "risk overstated; dental "
                                           "extraction urgent")
        self.assertFalse(out["prescriber_response"]["accept"])
        stats = self.svc.acceptance_stats()
        self.assertEqual((stats["interventions"], stats["accepted"]),
                         (1, 0))

    def test_intervention_needs_reason(self):
        with self.assertRaises(TeleReadError):
            self.svc.intervene("O1", pharmacist="pharm-1", day=0,
                               recommendation="x", reason="")


if __name__ == "__main__":
    unittest.main()
