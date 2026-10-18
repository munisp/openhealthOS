"""stockcheck tests (ADR-0018): ghost dispensing, shrinkage, tolerances,
feed tolerances — and that REVIEW asks questions without accusing."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from stockcheck import read_jsonl, reconcile, to_dict  # noqa: E402


def cl(fid, drug, q):
    return {"facility_id": fid, "item_code": drug, "quantity": q}


def disp(fid, drug, q, status="dispensed"):
    return {"facility_id": fid, "drug_code": drug, "quantity": q, "status": status}


def proc(fid, drug, q):
    return {"facility_id": fid, "drug_code": drug, "quantity": q}


class TestReconcile(unittest.TestCase):
    def test_clean_facility_is_ok(self):
        rows = reconcile([cl("F-1", "ACT", 100)], [disp("F-1", "ACT", 100)],
                         [proc("F-1", "ACT", 103)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].verdict, "OK")
        self.assertEqual(rows[0].flags, [])
        self.assertEqual(rows[0].shrinkage, 3)  # buffer stock, inside tolerance

    def test_ghost_dispensing_flags_review(self):
        # Billed for 500 tablets; pharmacy moved 0. That is the ghost question.
        rows = reconcile([cl("F-2", "ACT", 500)], [], [proc("F-2", "ACT", 600)])
        r = rows[0]
        self.assertEqual(r.verdict, "REVIEW")
        self.assertTrue(any("GHOST_DISPENSE" in f for f in r.flags))
        self.assertEqual(r.claim_variance, 500)

    def test_shrinkage_flags_review(self):
        rows = reconcile([], [disp("F-3", "AMOX", 6000)], [proc("F-3", "AMOX", 10000)])
        r = rows[0]
        self.assertEqual(r.verdict, "REVIEW")
        self.assertTrue(any("SHRINKAGE" in f for f in r.flags))
        self.assertEqual(r.shrinkage, 4000)

    def test_within_tolerance_stays_ok(self):
        # 4 units variance on 200 dispensed: inside 2%/5-unit tolerance.
        rows = reconcile([cl("F-1", "ACT", 204)], [disp("F-1", "ACT", 200)],
                         [proc("F-1", "ACT", 200)])
        self.assertEqual(rows[0].verdict, "OK")

    def test_pending_dispense_moves_no_stock(self):
        rows = reconcile([cl("F-1", "ACT", 100)],
                         [disp("F-1", "ACT", 100, status="pending")],
                         [proc("F-1", "ACT", 100)])
        self.assertEqual(rows[0].dispensed, 0)
        self.assertEqual(rows[0].verdict, "REVIEW")  # claimed, never dispensed

    def test_unprocured_dispense_questions_stock_source(self):
        rows = reconcile([], [disp("F-4", "ORS", 500)], [proc("F-4", "ORS", 100)])
        self.assertTrue(any("UNPROCURED_DISPENSE" in f for f in rows[0].flags))

    def test_review_is_a_question_not_an_accusation(self):
        rows = reconcile([cl("F-2", "ACT", 500)], [], [proc("F-2", "ACT", 600)])
        blob = json.dumps(to_dict(rows[0]))
        self.assertIn("?", blob)
        self.assertNotIn("THEFT", blob)
        self.assertNotIn("FRAUD", blob)

    def test_multi_facility_sorted(self):
        rows = reconcile(
            [cl("F-B", "ACT", 10), cl("F-A", "ACT", 10)],
            [disp("F-B", "ACT", 10), disp("F-A", "ACT", 10)],
            [proc("F-A", "ACT", 10), proc("F-B", "ACT", 10)])
        self.assertEqual([r.facility_id for r in rows], ["F-A", "F-B"])


class TestFeedTolerance(unittest.TestCase):
    def test_malformed_lines_skipped(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write('{"facility_id":"F-1","item_code":"ACT","quantity":5}\n')
            fh.write("not json at all\n\n")
            path = fh.name
        try:
            rows = read_jsonl(path)
            self.assertEqual(len(rows), 1)
        finally:
            os.unlink(path)

    def test_missing_file_is_honest_empty(self):
        self.assertEqual(read_jsonl("/nonexistent/feed.jsonl"), [])


class TestCLI(unittest.TestCase):
    def test_cli_reconcile_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            def w(name, rows):
                p = os.path.join(td, name)
                with open(p, "w") as fh:
                    for r in rows:
                        fh.write(json.dumps(r) + "\n")
                return p

            claims = w("claims.jsonl", [cl("F-9", "ACT", 300)])
            dispense = w("disp.jsonl", [disp("F-9", "ACT", 40)])
            procure = w("proc.jsonl", [proc("F-9", "ACT", 500)])
            out = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "stockcheck.py"),
                 "reconcile", "--claims", claims, "--dispensing", dispense,
                 "--procurement", procure],
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            report = json.loads(out.stdout)
            self.assertEqual(report["review"], 1)
            self.assertEqual(report["drugs"][0]["verdict"], "REVIEW")
            self.assertIn("question", report["note"])


if __name__ == "__main__":
    unittest.main()
