import os
import sys
import unittest

for pkg in ("teleread", "duelgrade", "aigov"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import DomainConfig, ReaderRegistry, ReadingService, TeleReadError
from duelgrade import CaptureQA, DuelGrader
from scorecard import clinician_card, scoreboard, ScorecardError

CFG = DomainConfig("ecg", scopes=("cardiac-reader",),
                   critical_findings=("stemi",),
                   sla_hours={"stat": 2, "urgent": 8, "routine": 24})
GRADES = ("normal", "abnormal")


def make_service():
    readers = ReaderRegistry()
    for rid in ("r1", "r2", "r3"):
        readers.register(rid, licence_id=f"L-{rid}", licence_expiry_day=400,
                         scopes=("cardiac-reader",))
    return ReadingService(CFG, readers), readers


def run_case(svc, case_id, *, reader, priority="routine", ordered=0,
             verified=0, critical=()):
    svc.order(case_id, priority=priority, question="?x", facility="f",
              referrer="n1", day=ordered)
    svc.assign(case_id, day=ordered, prefer=reader)
    svc.draft_report(case_id, reader_id=reader, findings="f",
                     impression="i", day=verified,
                     critical_findings=critical, template_version="ecg-v1")
    svc.verify_report(case_id, reader_id=reader, day=verified)


class TestClinicianCard(unittest.TestCase):
    def setUp(self):
        self.svc, self.readers = make_service()
        run_case(self.svc, "C1", reader="r1", priority="stat",
                 ordered=0, verified=0, critical=("stemi",))
        run_case(self.svc, "C2", reader="r1", priority="routine",
                 ordered=0, verified=2)
        run_case(self.svc, "C3", reader="r2", priority="stat",
                 ordered=0, verified=1)
        self.svc.acknowledge_critical("C1", day=0, by="r1")
        self.svc.peer_review("C1", reviewer="r2", day=1, score=3,
                             discrepancy="missed stemi",
                             direction="undercall")
        self.svc.peer_review("C2", reviewer="r2", day=3, score=1)
        self.svc.peer_review("C3", reviewer="r1", day=2, score=2,
                             discrepancy="minor axis call")

    def test_card_composes_the_engines(self):
        card = clinician_card(self.svc, "r1", as_of=3)
        self.assertEqual(card["workload"]["assigned_total"], 2)
        self.assertEqual(card["workload"]["by_state"]["reported-verified"],
                         2)
        self.assertEqual(card["turnaround"]["stat"]["n"], 1)
        self.assertEqual(card["turnaround"]["stat"]["within_sla"], 1)
        self.assertEqual(card["turnaround"]["routine"]["mean_hours"], 48.0)
        self.assertEqual(card["turnaround"]["routine"]["within_sla"], 0)
        pr = card["peer_review"]
        self.assertEqual(pr["reviews_of_my_reports"], 2)
        self.assertEqual(pr["score_distribution"][3], 1)
        self.assertEqual(pr["score_distribution"][1], 1)
        self.assertEqual(pr["clinically_significant"], 1)
        self.assertEqual(pr["reviews_by_me"], 1)
        self.assertEqual(card["critical"]["raised_on_my_cases"], 1)
        self.assertEqual(card["critical"]["acknowledged"], 1)

    def test_zero_denominators_are_honest(self):
        card = clinician_card(self.svc, "r3", as_of=3)
        self.assertEqual(card["workload"]["assigned_total"], 0)
        self.assertEqual(card["turnaround"], {})
        self.assertEqual(card["peer_review"]["reviews_of_my_reports"], 0)
        self.assertIsNone(card["peer_review"]["significant_rate"])

    def test_card_for_nobody_refuses(self):
        with self.assertRaises(ScorecardError):
            clinician_card(self.svc, "", as_of=3)

    def test_optional_qa_and_duel_extras(self):
        qa = CaptureQA("fundus", ("focus",))
        qa.assess("i1", day=0, by="r1", passed=("focus",))
        qa.assess("i2", day=0, by="r1", passed=(), failed=("focus",))
        duel = DuelGrader(self.readers, scope="cardiac-reader",
                          grades=GRADES)
        duel.submit_grade("G1", grade="normal", by="r1", day=0)
        duel.submit_grade("G1", grade="abnormal", by="r2", day=0)
        duel.resolve("G1", day=1)
        card = clinician_card(self.svc, "r1", as_of=3, qa=qa, duel=duel)
        self.assertEqual(card["capture_qa"]["n"], 2)
        self.assertEqual(card["capture_qa"]["ungradable_rate"], 0.5)
        self.assertEqual(card["grading_concordance"]["paired"], 1)
        self.assertEqual(card["grading_concordance"]["concordant"], 0)
        # absent from the extras → honest zero, not a missing key
        card3 = clinician_card(self.svc, "r3", as_of=3, qa=qa, duel=duel)
        self.assertEqual(card3["capture_qa"]["n"], 0)
        self.assertIsNone(card3["grading_concordance"]["concordance"])

    def test_scoreboard_covers_everyone_who_touched_the_service(self):
        board = scoreboard(self.svc, as_of=3)
        self.assertEqual(sorted(board["readers"]), ["r1", "r2"])
        self.assertEqual(board["domain"], "ecg")
        r2 = board["readers"]["r2"]
        self.assertEqual(r2["peer_review"]["reviews_by_me"], 2)
        self.assertEqual(r2["peer_review"]["reviews_of_my_reports"], 1)


if __name__ == "__main__":
    unittest.main()
