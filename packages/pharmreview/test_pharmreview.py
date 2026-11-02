import os
import sys
import unittest

for pkg in ("teleread", "drugkb", "steward"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from pharmreview import PharmacyReview
from steward import TimeoutClock


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


class TestKnowledgeBaseWiring(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("pharm-1", licence_id="PCN-1", licence_expiry_day=400,
                   scopes=("clinical-pharmacist",))
        self.svc = PharmacyReview(r)

    def test_contraindicated_finding_escalates_to_stat(self):
        # enoxaparin at eGFR 10: the renal table says contraindicated
        out = self.svc.submit_order(
            "O1", patient_id="p1", drug="enoxaparin", dose=40,
            route="sc", risk_class="anticoagulant", prescriber="mo-1",
            facility="phc-1", day=0, egfr=10)
        self.assertEqual(out["priority"], "stat")
        self.assertTrue(out["meta"]["contraindicated"])
        safety = self.svc.orders["O1"]["safety"]
        self.assertEqual(safety["findings"][0]["kind"], "renal")
        self.assertEqual(safety["formulary_version"], "formulary-ng@1.0.0")

    def test_major_interaction_is_loud_but_not_stat(self):
        # warfarin × NSAID class: major — findings recorded, queue stays
        # urgent; only contraindications jump the queue
        out = self.svc.submit_order(
            "O1b", patient_id="p1b", drug="warfarin", dose=5, route="po",
            risk_class="anticoagulant", prescriber="mo-1",
            facility="phc-1", day=0, current_meds=("ibuprofen",),
            egfr=25)
        self.assertEqual(out["priority"], "urgent")
        self.assertFalse(out["meta"]["contraindicated"])
        kinds = {f["kind"] for f in self.svc.orders["O1b"]["safety"]
                 ["findings"]}
        self.assertIn("interaction", kinds)   # warfarin × NSAID class

    def test_allergy_flag_is_automatic(self):
        out = self.svc.submit_order(
            "O2", patient_id="p2", drug="gentamicin", dose=240,
            route="iv", risk_class="aminoglycoside", prescriber="mo-1",
            facility="phc-1", day=0, allergies=("aminoglycoside",))
        self.assertEqual(out["priority"], "stat")
        kinds = {f["kind"] for f in self.svc.orders["O2"]["safety"]
                 ["findings"]}
        self.assertIn("allergy", kinds)

    def test_clean_order_stays_urgent_and_honest(self):
        out = self.svc.submit_order(
            "O3", patient_id="p3", drug="warfarin", dose=5, route="po",
            risk_class="anticoagulant", prescriber="mo-1",
            facility="phc-1", day=0)
        self.assertEqual(out["priority"], "urgent")
        self.assertFalse(out["meta"]["contraindicated"])
        self.assertEqual(out["meta"]["safety_findings"], 0)

    def test_timeout_queues_urgent_and_idempotent(self):
        clock = TimeoutClock()
        clock.start_course("ABX-1", patient_id="p1",
                           drug_code="NG-CIPRO500", route="iv", day=0,
                           indication="pyelonephritis", prescriber="mo-1")
        due = clock.timeouts_due(as_of=3)
        self.assertEqual(len(due), 1)
        out = self.svc.queue_timeout(due[0], day=3)
        self.assertFalse(out["already_queued"])
        self.assertEqual(out["priority"], "urgent")
        self.assertEqual(out["case_id"], "timeout-ABX-1-due2")
        again = self.svc.queue_timeout(due[0], day=3)
        self.assertTrue(again["already_queued"])
        # a reviewed-and-continued course re-arms and queues anew
        clock.review_timeout("ABX-1", day=3, by="pharm-1",
                             outcome="continue", note="await cultures")
        due2 = clock.timeouts_due(as_of=5)
        out2 = self.svc.queue_timeout(due2[0], day=5)
        self.assertEqual(out2["case_id"], "timeout-ABX-1-due5")
        self.assertFalse(out2["already_queued"])


if __name__ == "__main__":
    unittest.main()
