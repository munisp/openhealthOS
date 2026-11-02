import os
import sys
import unittest

for pkg in ("teleread", "aigov"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry
from aigov import ModelRegistry, AIGovError
from duelgrade import (CaptureQA, DuelGrader, DuelError, recall_plan,
                       SURVEILLANCE_MONTHS)

CHECKS = ("field-definition", "focus", "illumination", "media-clarity")
GRADES = ("no-dr", "mild-npdr", "moderate-npdr", "severe-npdr",
          "pdr", "maculopathy")


def make_readers():
    r = ReaderRegistry()
    r.register("gr-1", licence_id="G1", licence_expiry_day=400,
               scopes=("dr-grader",))
    r.register("gr-2", licence_id="G2", licence_expiry_day=400,
               scopes=("dr-grader",))
    r.register("gr-senior", licence_id="G3", licence_expiry_day=400,
               scopes=("dr-grader",))
    r.register("gr-expired", licence_id="G4", licence_expiry_day=5,
               scopes=("dr-grader",))
    r.register("rad-1", licence_id="R1", licence_expiry_day=400,
               scopes=("chest",))
    return r


class TestCaptureQA(unittest.TestCase):
    def setUp(self):
        self.qa = CaptureQA("fundus", CHECKS)

    def test_every_check_must_be_accounted_for(self):
        with self.assertRaises(DuelError):  # unassessed checks
            self.qa.assess("img1", day=0, by="ph-1",
                           passed=("focus", "illumination"))
        with self.assertRaises(DuelError):  # unknown check
            self.qa.assess("img1", day=0, by="ph-1",
                           passed=CHECKS + ("vibes",))
        with self.assertRaises(DuelError):  # pass and fail at once
            self.qa.assess("img1", day=0, by="ph-1",
                           passed=CHECKS, failed=("focus",))

    def test_unsigned_qa_is_no_qa(self):
        with self.assertRaises(DuelError):
            self.qa.assess("img1", day=0, by="", passed=CHECKS)
        with self.assertRaises(DuelError):
            self.qa.assess("", day=0, by="ph-1", passed=CHECKS)

    def test_retake_is_a_new_image_not_a_rewrite(self):
        self.qa.assess("img1", day=0, by="ph-1", passed=CHECKS[:2],
                       failed=CHECKS[2:])
        self.assertFalse(self.qa.gradable("img1"))
        with self.assertRaises(DuelError):
            self.qa.assess("img1", day=1, by="ph-1", passed=CHECKS)
        self.qa.assess("img2", day=1, by="ph-1", passed=CHECKS)
        self.assertTrue(self.qa.gradable("img2"))

    def test_absence_is_not_gradable(self):
        self.assertFalse(self.qa.gradable("never-seen"))

    def test_report_ships_denominators(self):
        self.qa.assess("i1", day=0, by="ph-1", passed=CHECKS)
        self.qa.assess("i2", day=0, by="ph-1", passed=CHECKS[:3],
                       failed=("media-clarity",))
        self.qa.assess("i3", day=0, by="ph-2", passed=CHECKS[:1],
                       failed=("focus", "illumination", "media-clarity"))
        rep = self.qa.report()
        self.assertEqual(rep["assessed"], 3)
        self.assertEqual(rep["ungradable"], 2)
        self.assertAlmostEqual(rep["ungradable_rate"], round(2 / 3, 3))
        self.assertEqual(rep["failure_modes"]["media-clarity"], 2)
        self.assertEqual(rep["by_assessor"]["ph-2"]["ungradable_rate"], 1.0)
        self.assertEqual(rep["by_assessor"]["ph-1"]["n"], 2)


class TestDuelGrader(unittest.TestCase):
    def setUp(self):
        self.readers = make_readers()
        self.duel = DuelGrader(self.readers, scope="dr-grader",
                               grades=GRADES)

    def test_fail_closed_inputs(self):
        with self.assertRaises(DuelError):  # unknown grade
            self.duel.submit_grade("C1", grade="alien", by="gr-1", day=0)
        with self.assertRaises(DuelError):  # unsigned
            self.duel.submit_grade("C1", grade="pdr", by="", day=0)
        with self.assertRaises(DuelError):  # expired licence
            self.duel.submit_grade("C1", grade="pdr", by="gr-expired",
                                   day=10)
        with self.assertRaises(DuelError):  # wrong craft
            self.duel.submit_grade("C1", grade="pdr", by="rad-1", day=0)

    def test_self_agreement_is_not_concordance(self):
        self.duel.submit_grade("C1", grade="pdr", by="gr-1", day=0)
        with self.assertRaises(DuelError):
            self.duel.submit_grade("C1", grade="pdr", by="gr-1", day=0)

    def test_one_grade_is_an_opinion(self):
        self.duel.submit_grade("C1", grade="pdr", by="gr-1", day=0)
        with self.assertRaises(DuelError):
            self.duel.resolve("C1", day=1)
        with self.assertRaises(DuelError):
            self.duel.final_grade("C1")

    def test_agreement_is_final_and_immutable(self):
        self.duel.submit_grade("C1", grade="pdr", by="gr-1", day=0)
        self.duel.submit_grade("C1", grade="pdr", by="gr-2", day=0)
        res = self.duel.resolve("C1", day=1)
        self.assertEqual(res["status"], "final-agreement")
        self.assertEqual(self.duel.final_grade("C1"), "pdr")
        with self.assertRaises(DuelError):  # no piling on after final
            self.duel.submit_grade("C1", grade="pdr", by="gr-senior",
                                   day=2)
        with self.assertRaises(DuelError):  # no re-running concordance
            self.duel.resolve("C1", day=2)

    def test_disagreement_routes_to_a_THIRD_pair_of_eyes(self):
        self.duel.submit_grade("C1", grade="moderate-npdr", by="gr-1",
                               day=0)
        self.duel.submit_grade("C1", grade="pdr", by="gr-2", day=0)
        res = self.duel.resolve("C1", day=1)
        self.assertEqual(res["status"], "awaiting-arbitration")
        with self.assertRaises(DuelError):  # dispute decides nothing yet
            self.duel.final_grade("C1")
        with self.assertRaises(DuelError):  # not a recount by grader 1
            self.duel.arbitrate("C1", grade="pdr", by="gr-1", day=2)
        with self.assertRaises(DuelError):  # nor by grader 2
            self.duel.arbitrate("C1", grade="pdr", by="gr-2", day=2)
        with self.assertRaises(DuelError):  # arbiter needs the craft
            self.duel.arbitrate("C1", grade="pdr", by="rad-1", day=2)
        arb = self.duel.arbitrate("C1", grade="pdr", by="gr-senior",
                                  day=2)
        self.assertEqual(arb["status"], "final-arbitrated")
        self.assertEqual(arb["final"]["matched"], "gr-2")
        self.assertEqual(self.duel.final_grade("C1"), "pdr")

    def test_no_arbitration_without_disagreement(self):
        self.duel.submit_grade("C1", grade="no-dr", by="gr-1", day=0)
        with self.assertRaises(DuelError):
            self.duel.arbitrate("C1", grade="pdr", by="gr-senior", day=1)

    def test_ai_first_read_only_through_governance(self):
        bare = DuelGrader(self.readers, scope="dr-grader", grades=GRADES)
        with self.assertRaises(DuelError):  # no registry, no AI
            bare.submit_ai_grade("C1", grade="pdr", model_id="dr-ai",
                                 version="2.0", day=0)
        models = ModelRegistry()
        governed = DuelGrader(self.readers, scope="dr-grader",
                              grades=GRADES, model_registry=models)
        with self.assertRaises(AIGovError):  # ghost model
            governed.submit_ai_grade("C1", grade="pdr", model_id="ghost",
                                     version="9", day=0)
        models.register("dr-ai", version="2.0", task="dr-grade",
                        licence_class="embed-clean",
                        evidence_ref="doi:10.x/dr")
        with self.assertRaises(DuelError):  # off-label grade
            governed.submit_ai_grade("C1", grade="alien",
                                     model_id="dr-ai", version="2.0",
                                     day=0)
        governed.submit_ai_grade("C1", grade="pdr", model_id="dr-ai",
                                 version="2.0", day=0)
        governed.submit_grade("C1", grade="pdr", by="gr-1", day=0)
        res = governed.resolve("C1", day=1)
        self.assertEqual(res["status"], "final-agreement")
        self.assertIn("ai:dr-ai@2.0", res["final"]["agreed_by"])

    def test_agreement_report_denominators(self):
        for cid, g1, g2 in (("C1", "pdr", "pdr"),
                            ("C2", "no-dr", "mild-npdr"),
                            ("C3", "no-dr", "no-dr")):
            self.duel.submit_grade(cid, grade=g1, by="gr-1", day=0)
            self.duel.submit_grade(cid, grade=g2, by="gr-2", day=0)
            self.duel.resolve(cid, day=1)
        self.duel.arbitrate("C2", grade="mild-npdr", by="gr-senior",
                            day=2)
        rep = self.duel.agreement_report()
        self.assertEqual(rep["cases_graded"], 3)
        self.assertEqual(rep["agreed"], 2)
        self.assertEqual(rep["arbitrated"], 1)
        self.assertAlmostEqual(rep["agreement_rate"], round(2 / 3, 3))
        self.assertEqual(rep["per_grader"]["gr-1"]["paired"], 3)
        # the arbiter cast no pairwise grade, so no concordance entry
        self.assertNotIn("gr-senior", rep["per_grader"])


class TestRecallPlan(unittest.TestCase):
    def test_unknown_grade_refuses(self):
        with self.assertRaises(DuelError):
            recall_plan("alien", from_day=0)

    def test_intervals_and_due_day(self):
        plan = recall_plan("pdr", from_day=10)
        self.assertEqual(plan["interval_months"], 1)
        self.assertEqual(plan["due_day"], 40)
        plan = recall_plan("no-dr", from_day=0)
        self.assertEqual(plan["due_day"], 360)
        # every graded DR patient has a recall — referral grades too
        for g in GRADES:
            self.assertIn(g, SURVEILLANCE_MONTHS)


if __name__ == "__main__":
    unittest.main()
