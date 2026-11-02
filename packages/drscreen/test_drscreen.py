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




class TestPregnancyPathway(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        for g in ("gr-1", "gr-2"):
            r.register(g, licence_id="G", licence_expiry_day=400,
                       scopes=("dr-grader",))
        self.svc = DRScreening(r)

    def _graded(self, pid, grade, day=0):
        self.svc.assess_capture(f"i-{pid}", day=day, by="ph",
                                passed=FC)
        self.svc.screen(pid, eye="both", image_ref=f"i-{pid}",
                        facility="f", referrer="n1", day=day)
        self.svc.record_grade(pid, grade=grade, by="gr-1", day=day)
        return self.svc.record_grade(pid, grade=grade, by="gr-2",
                                     day=day)

    def test_pregnancy_overrides_interval_and_preserves_grade_plan(self):
        out = self._graded("w1", "no-dr")
        self.assertEqual(out["recall"]["interval_months"], 12)
        self.svc.mark_pregnant("w1", day=5, lmp_day=5 - 60)
        plan = self.svc.recall["w1"]
        self.assertEqual(plan["interval_months"], 3)
        self.assertEqual(plan["due_day"], 5 + 90)
        self.assertTrue(plan["pregnancy_override"])
        self.assertEqual(plan["grade_plan_months"], 12)  # preserved

    def test_pregnancy_at_finalize_overrides(self):
        self.svc.mark_pregnant("w2", day=0, lmp_day=-30)
        out = self._graded("w2", "mild-npdr")
        self.assertTrue(out["pregnancy_override"])
        self.assertEqual(out["recall"]["interval_months"], 3)

    def test_lmp_sanity_and_double_mark_refuse(self):
        with self.assertRaises(TeleReadError):
            self.svc.mark_pregnant("w3", day=0, lmp_day=-400)
        self.svc.mark_pregnant("w3", day=0, lmp_day=-30)
        with self.assertRaises(TeleReadError):
            self.svc.mark_pregnant("w3", day=1, lmp_day=-29)

    def test_end_pregnancy_restores_grade_clock(self):
        self._graded("w4", "mild-npdr")
        self.svc.mark_pregnant("w4", day=5, lmp_day=-30)
        self.assertEqual(self.svc.recall["w4"]["interval_months"], 3)
        self.svc.end_pregnancy("w4", day=250, outcome="delivered")
        plan = self.svc.recall["w4"]
        self.assertNotIn("pregnancy_override", plan)
        self.assertEqual(plan["interval_months"], 12)  # mild again
        self.assertEqual(plan["due_day"], 250 + 360)
        with self.assertRaises(TeleReadError):
            self.svc.end_pregnancy("w4", day=251, outcome="delivered")

    def test_pregnancy_does_not_slow_a_referral_grade(self):
        # PDR is urgent whether or not she is pregnant — pregnancy
        # accelerates surveillance; it never decelerates referral
        self.svc.mark_pregnant("w5", day=0, lmp_day=-30)
        out = self._graded("w5", "pdr")
        self.assertTrue(out["referred"])
        self.assertNotIn("pregnancy_override", out)
        self.assertEqual(out["recall"]["interval_months"], 1)


class TestTreatmentTracking(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        for g in ("gr-1", "gr-2"):
            r.register(g, licence_id="G", licence_expiry_day=400,
                       scopes=("dr-grader",))
        self.svc = DRScreening(r)

    def _referred(self, pid, grade="pdr", day=0):
        self.svc.assess_capture(f"i-{pid}", day=day, by="ph",
                                passed=FC)
        self.svc.screen(pid, eye="both", image_ref=f"i-{pid}",
                        facility="f", referrer="n1", day=day)
        self.svc.record_grade(pid, grade=grade, by="gr-1", day=day)
        self.svc.record_grade(pid, grade=grade, by="gr-2", day=day)

    def test_treatment_requires_referral_grade(self):
        self._referred("m1", grade="mild-npdr")
        with self.assertRaises(TeleReadError):
            self.svc.record_treatment("m1", kind="laser-prp",
                                      eye="left", day=10, by="oph-1")
        with self.assertRaises(TeleReadError):   # unsited
            self.svc.record_treatment("m1", kind="laser-prp",
                                      eye="the-left-one", day=10,
                                      by="oph-1")

    def test_treatment_closes_cascade_via_attendance(self):
        self._referred("t1")
        self.svc.advance("t1", "attended", day=10)
        ep = self.svc.record_treatment("t1", kind="laser-prp",
                                       eye="left", day=10, by="oph-1")
        self.assertEqual(self.svc.cascade["t1"]["treated"], 10)
        self.assertEqual(ep["kind"], "laser-prp")

    def test_injection_series_counts_and_flags_gaps(self):
        self._referred("t2", grade="maculopathy")
        self.svc.advance("t2", "attended", day=5)
        for d in (5, 33, 61):
            self.svc.record_treatment("t2", kind="anti-vegf-injection",
                                      eye="right", day=d, by="oph-1")
        self.svc.record_treatment("t2", kind="anti-vegf-injection",
                                  eye="right", day=200, by="oph-1")
        series = self.svc.injection_series("t2", "right")
        self.assertEqual(series["injections"], 4)
        self.assertEqual(series["gaps_days"], [28, 28, 139])
        self.assertEqual(series["broken_gaps"], [139])
        with self.assertRaises(TeleReadError):   # series append-only
            self.svc.record_treatment("t2", kind="anti-vegf-injection",
                                      eye="right", day=100, by="oph-1")

    def test_treatment_due_overdue_first(self):
        self._referred("t3", grade="maculopathy")
        self.svc.advance("t3", "attended", day=0)
        self.svc.record_treatment("t3", kind="anti-vegf-injection",
                                  eye="left", day=0, by="oph-1")
        self._referred("t4", grade="pdr")   # never treated at all
        due = self.svc.treatment_due(as_of=60)
        reasons = {d["patient_id"]: d["reason"] for d in due}
        self.assertEqual(reasons["t3"], "injection-due")
        self.assertEqual(reasons["t4"], "referred-never-treated")
        self.assertEqual(due[0]["patient_id"], "t4")  # most overdue


if __name__ == "__main__":
    unittest.main()
