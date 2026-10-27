import unittest
from stockledger import Ledger, StockError


class TestStock(unittest.TestCase):
    def setUp(self):
        self.l = Ledger("phc-1")
        self.l.receive("amoxicillin-dt", 500, day=1, source="lga-store")

    def test_receive_and_issue(self):
        self.l.issue("amoxicillin-dt", 100, day=2, protocol_action="give-oral-amoxicillin-dt")
        self.assertEqual(self.l.balance("amoxicillin-dt"), 400)

    def test_never_negative_fail_closed(self):
        with self.assertRaises(StockError):
            self.l.issue("amoxicillin-dt", 501, day=2)
        self.assertEqual(self.l.balance("amoxicillin-dt"), 500)

    def test_issue_at_zero_records_stockout_event(self):
        l = Ledger("phc-2")
        with self.assertRaises(StockError):
            l.issue("ors", 4, day=5)
        with self.assertRaises(StockError):
            l.issue("ors", 4, day=5)   # same day counted once
        with self.assertRaises(StockError):
            l.issue("ors", 4, day=6)
        self.assertEqual(l.stockout_days["ors"], [5, 6])

    def test_issue_to_zero_marks_stockout_day(self):
        self.l.issue("amoxicillin-dt", 500, day=3)
        self.assertEqual(self.l.stockout_days["amoxicillin-dt"], [3])

    def test_corrections_are_reversals_not_edits(self):
        self.l.issue("amoxicillin-dt", 100, day=2)
        idx = len(self.l.entries) - 1
        self.l.correct(idx, day=3, reason="counting error", officer="pharm-tech")
        self.assertEqual(self.l.balance("amoxicillin-dt"), 500)
        with self.assertRaises(StockError):
            self.l.correct(idx, day=3, reason="", officer="x")
        with self.assertRaises(StockError):
            self.l.correct(999, day=3, reason="ghost", officer="x")

    def test_rate_learning_then_ok(self):
        l = Ledger("phc-3")
        l.receive("zinc", 1000, day=1, source="store")
        for d in range(2, 12):   # 10 issue days
            l.issue("zinc", 10, day=d)
        r = l.consumption_rate("zinc", as_of=32, window=30)
        self.assertEqual(r["state"], "ok")
        self.assertAlmostEqual(r["rate"], 100 / 30.0)
        thin = Ledger("phc-4")
        thin.receive("zinc", 100, day=1, source="store")
        thin.issue("zinc", 5, day=2)
        self.assertEqual(thin.consumption_rate("zinc", as_of=32)["state"],
                         "learning")

    def test_report_reorder_flag(self):
        l = Ledger("phc-5")
        l.receive("act", 100, day=1, source="store")
        for d in range(2, 12):
            l.issue("act", 3, day=d)   # rate 1/day, balance 70 -> 70 days
        rep = l.report(["act"], as_of=32)
        self.assertFalse(rep["items"]["act"]["reorder_flag"])
        for d in range(12, 32):
            l.issue("act", 2, day=d)   # 40 more units, balance 30
        rep = l.report(["act"], as_of=32)
        row = rep["items"]["act"]
        self.assertEqual(row["balance"], 30)
        self.assertTrue(row["reorder_flag"])

    def test_bad_qty_refused(self):
        with self.assertRaises(StockError):
            self.l.receive("x", 0, day=1, source="s")
        with self.assertRaises(StockError):
            self.l.issue("amoxicillin-dt", -5, day=2)


if __name__ == "__main__":
    unittest.main()
