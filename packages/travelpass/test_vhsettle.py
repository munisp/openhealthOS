"""Tests for vhsettle — settlement files with checksums, reconciliation
with named breaks against a ppm tolerance, and duplicate auto-refunds
that carry their evidence."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhsettle import (SettleError, auto_refund_duplicates,  # noqa: E402
                      reconcile, settlement_file)


def _txns(n=1000, *, psp="stripe", day=10):
    return [{"ref": "TX-%04d" % i, "amount_minor": 2500, "psp": psp,
             "day": day} for i in range(n)]


class TestSettlementFile(unittest.TestCase):
    def test_file_has_rows_totals_checksum(self):
        f = settlement_file(_txns(3), day=10, psp="stripe")
        self.assertEqual(f["count"], 3)
        self.assertEqual(f["total_minor"], 7500)
        self.assertEqual(len(f["file_sha256"]), 64)
        self.assertTrue(f["file_id"].startswith("STL-stripe-10-"))

    def test_file_is_scoped_to_psp_and_day(self):
        txns = _txns(2, psp="stripe") + _txns(1, psp="adyen") + \
            _txns(1, day=11)
        f = settlement_file(txns, day=10, psp="stripe")
        self.assertEqual(f["count"], 2)


class TestReconcile(unittest.TestCase):
    def test_clean_recon(self):
        txns = _txns(1000)
        psp_rows = [{"ref": t["ref"], "amount_minor": t["amount_minor"]}
                    for t in txns]
        out = reconcile(txns, day=10, psp="stripe", psp_rows=psp_rows)
        self.assertFalse(out["breach"])
        self.assertEqual(out["break_count"], 0)
        self.assertEqual(out["break_rate_ppm"], 0)

    def test_one_break_in_1000_is_exactly_at_tolerance(self):
        txns = _txns(1000)
        psp_rows = [{"ref": t["ref"], "amount_minor": t["amount_minor"]}
                    for t in txns if t["ref"] != "TX-0500"]
        out = reconcile(txns, day=10, psp="stripe", psp_rows=psp_rows)
        self.assertEqual(out["break_rate_ppm"], 1000)
        self.assertFalse(out["breach"])  # at tolerance, not past it
        self.assertEqual(out["breaks"][0]["kind"], "missing-in-psp")

    def test_two_breaks_in_1000_breaches(self):
        txns = _txns(1000)
        drop = {"TX-0001", "TX-0002"}
        psp_rows = [{"ref": t["ref"], "amount_minor": t["amount_minor"]}
                    for t in txns if t["ref"] not in drop]
        out = reconcile(txns, day=10, psp="stripe", psp_rows=psp_rows)
        self.assertTrue(out["breach"])
        self.assertIn("escalate", out["note"])

    def test_amount_mismatch_is_named(self):
        txns = _txns(3)
        psp_rows = [{"ref": t["ref"], "amount_minor": t["amount_minor"]}
                    for t in txns]
        psp_rows[1]["amount_minor"] = 999
        out = reconcile(txns, day=10, psp="stripe", psp_rows=psp_rows)
        self.assertEqual(out["breaks"][0]["kind"], "amount-mismatch")

    def test_empty_day_is_stated_not_assumed(self):
        out = reconcile([], day=10, psp="stripe", psp_rows=[])
        self.assertEqual(out["note"], "no transactions — stated, not "
                                      "assumed")


class TestAutoRefund(unittest.TestCase):
    def test_duplicate_refunded_first_kept(self):
        txns = [{"ref": "A", "amount_minor": 2500, "payer_key": "P1",
                 "day": 10},
                {"ref": "B", "amount_minor": 2500, "payer_key": "P1",
                 "day": 10}]
        refunds = auto_refund_duplicates(txns, day=10)
        self.assertEqual(len(refunds), 1)
        self.assertEqual(refunds[0]["duplicate_ref"], "B")
        self.assertEqual(refunds[0]["kept_ref"], "A")
        self.assertIn("duplicate charge", refunds[0]["reason"])

    def test_outside_window_is_not_a_duplicate(self):
        txns = [{"ref": "A", "amount_minor": 2500, "payer_key": "P1",
                 "day": 10},
                {"ref": "B", "amount_minor": 2500, "payer_key": "P1",
                 "day": 13}]
        self.assertEqual(auto_refund_duplicates(txns, day=13), [])

    def test_different_amounts_are_not_duplicates(self):
        txns = [{"ref": "A", "amount_minor": 2500, "payer_key": "P1",
                 "day": 10},
                {"ref": "B", "amount_minor": 5000, "payer_key": "P1",
                 "day": 10}]
        self.assertEqual(auto_refund_duplicates(txns, day=10), [])


if __name__ == "__main__":
    unittest.main()
