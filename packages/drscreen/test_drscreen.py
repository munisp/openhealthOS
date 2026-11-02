import os
import sys
import unittest

for pkg in ("teleread", "aigov", "duelgrade"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from aigov import ModelRegistry, AIGovError
from duelgrade import DuelError
from drscreen import DRScreening, FUNDUS_CHECKS as FC


class TestDR(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("gr-1", licence_id="G1", licence_expiry_day=400,
                   scopes=("dr-grader",))
        r.register("gr-2", licence_id="G2", licence_expiry_day=400,
                   scopes=("dr-grader",))
        r.register("gr-senior", licence_id="G3", licence_expiry_day=400,
                   scopes=("dr-grader",))
        self.models = ModelRegistry()
        self.models.register("dr-ai", version="2.0", task="dr-grade",
                             licence_class="embed-clean",
                             evidence_ref="doi:10.x/dr")
        self.svc = DRScreening(r, self.models)

    def _good_capture(self, ref, day=0):
        self.svc.assess_capture(ref, day=day, by="photog-1", passed=FC)

    def test_no_gradable_capture_no_screen(self):
        # never assessed
        with self.assertRaises(TeleReadError):
            self.svc.screen("p1", eye="both", image_ref="i0", facility="f",
                            referrer="n1", day=0)
        # assessed and ungradable: a FAILED screen, not a negative
        self.svc.assess_capture("i0", day=0, by="photog-1",
                                passed=FC[:2], failed=FC[2:])
        with self.assertRaises(TeleReadError):
            self.svc.screen("p1", eye="both", image_ref="i0", facility="f",
                            referrer="n1", day=0)
        # retake is a new image; then the screen proceeds
        self._good_capture("i0b")
        out = self.svc.screen("p1", eye="both", image_ref="i0b",
                              facility="f", referrer="n1", day=0)
        self.assertEqual(out["case_id"], "dr-p1-0")

    def test_ai_draft_governed(self):
        bare = DRScreening(ReaderRegistry())
        bare.assess_capture("i1", day=0, by="photog-1", passed=FC)
        with self.assertRaises(TeleReadError):
            bare.screen("p1", eye="both", image_ref="i1", facility="f",
                        referrer="n1", day=0,
                        ai_draft={"model_id": "dr-ai", "version": "2.0",
                                  "output": {}})
        self._good_capture("i1")
        with self.assertRaises(AIGovError):
            self.svc.screen("p1", eye="both", image_ref="i1", facility="f",
                            referrer="n1", day=0,
                            ai_draft={"model_id": "ghost", "version": "9",
                                      "output": {}})

    def test_two_grader_agreement_drives_cascade(self):
        self._good_capture("i1")
        self.svc.screen("p1", eye="both", image_ref="i1", facility="f",
                        referrer="n1", day=0)
        with self.assertRaises(TeleReadError):
            self.svc.advance("p1", "treated", day=5)
        first = self.svc.record_grade("p1", grade="pdr", by="gr-1", day=2)
        self.assertEqual(first["status"], "awaiting-second-grade")
        self.assertNotIn("referred", self.svc.cascade["p1"])
        second = self.svc.record_grade("p1", grade="pdr", by="gr-2", day=2)
        self.assertEqual(second["resolution"], "final-agreement")
        self.assertTrue(second["referred"])
        self.assertEqual(self.svc.cascade["p1"]["referred"], 2)
        self.assertEqual(self.svc.recall["p1"]["interval_months"], 1)
        self.svc.advance("p1", "attended", day=10)
        self.svc.advance("p1", "treated", day=10)
        # a second patient, concordant moderate — also a referral grade
        self._good_capture("i2")
        self.svc.screen("p2", eye="left", image_ref="i2", facility="f",
                        referrer="n1", day=0)
        self.svc.record_grade("p2", grade="moderate-npdr", by="gr-1",
                              day=2)
        self.svc.record_grade("p2", grade="moderate-npdr", by="gr-2",
                              day=2)
        rep = self.svc.cascade_report()
        self.assertEqual(rep["screened"], 2)
        self.assertEqual(rep["referred"], 2)
        self.assertEqual(rep["treated"], 1)  # attrition visible

    def test_dispute_waits_for_the_arbiter_but_critical_is_loud(self):
        self._good_capture("i1")
        self.svc.screen("p1", eye="both", image_ref="i1", facility="f",
                        referrer="n1", day=0)
        self.svc.record_grade("p1", grade="moderate-npdr", by="gr-1",
                              day=2)
        out = self.svc.record_grade("p1", grade="pdr", by="gr-2", day=2)
        self.assertEqual(out["resolution"], "awaiting-arbitration")
        self.assertEqual(out["critical_pending_arbitration"], ["pdr"])
        # no referral fires on a disputed grade
        self.assertNotIn("referred", self.svc.cascade["p1"])
        with self.assertRaises(DuelError):  # a prior grader cannot judge
            self.svc.arbitrate("p1", grade="pdr", by="gr-1", day=3)
        arb = self.svc.arbitrate("p1", grade="pdr", by="gr-senior", day=3)
        self.assertTrue(arb["referred"])
        self.assertEqual(self.svc.cascade["p1"]["referred"], 3)

    def test_surveillance_due_is_overdue_first(self):
        for pid, ref, grade in (("p1", "i1", "no-dr"),
                                ("p2", "i2", "pdr")):
            self._good_capture(ref)
            self.svc.screen(pid, eye="both", image_ref=ref, facility="f",
                            referrer="n1", day=0)
            self.svc.record_grade(pid, grade=grade, by="gr-1", day=1)
            self.svc.record_grade(pid, grade=grade, by="gr-2", day=1)
        due = self.svc.surveillance_due(as_of=100)
        self.assertEqual([d["patient_id"] for d in due], ["p2"])
        due = self.svc.surveillance_due(as_of=400)
        self.assertEqual([d["patient_id"] for d in due], ["p2", "p1"])

    def test_no_image_no_screen(self):
        with self.assertRaises(TeleReadError):
            self.svc.screen("p9", eye="left", image_ref="", facility="f",
                            referrer="n1", day=0)


if __name__ == "__main__":
    unittest.main()
