"""Tests for vhfund — five-bucket allocation at posting time, bucket
spend discipline, and the governed insolvency backstop."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhfund import FundError, FundLedger, FUND_RULES  # noqa: E402


class TestAllocation(unittest.TestCase):
    def test_premium_splits_five_ways_at_the_door(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        out = fund.post_premium("VHC-1", amount_minor=5000, day=1)
        self.assertEqual(out["allocated"],
                         {"claims_medevac": 2250,
                          "clinical_capacity": 1250,
                          "surveillance": 500, "platform": 500,
                          "prevention": 500})
        self.assertEqual(sum(out["allocated"].values()), 5000)

    def test_rounding_dust_lands_in_claims_never_vanishes(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        out = fund.post_premium("VHC-2", amount_minor=999, day=1)
        self.assertEqual(sum(out["allocated"].values()), 999)

    def test_buckets_must_sum_to_100(self):
        for rules in FUND_RULES.values():
            self.assertEqual(sum(rules["buckets"].values()), 100)


class TestSpend(unittest.TestCase):
    def test_spend_names_bucket_and_purpose(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        fund.post_premium("VHC-1", amount_minor=100_000, day=1)
        out = fund.spend("claims_medevac", amount_minor=30_000,
                         purpose="claim CLM-1 remittance", day=2)
        self.assertEqual(out["balance_minor"], 15_000)
        with self.assertRaises(FundError):
            fund.spend("claims_medevac", amount_minor=1, purpose="",
                       day=2)

    def test_bucket_overdraft_refuses(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        fund.post_premium("VHC-1", amount_minor=10_000, day=1)
        with self.assertRaises(FundError):
            fund.spend("surveillance", amount_minor=1001,
                       purpose="lab kits", day=2)
        # the claims bucket may not bail it out silently
        self.assertEqual(fund._balance("claims_medevac"), 4500)

    def test_unknown_bucket_refuses(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        with self.assertRaises(FundError):
            fund.spend("christmas_party", amount_minor=1,
                       purpose="x", day=1)


class TestBackstop(unittest.TestCase):
    def test_activation_needs_who_why_and_notice(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        with self.assertRaises(FundError):
            fund.activate_backstop(by="md", reason="insurer insolvent",
                                   notice_ref="", day=10)

    def test_backstop_keeps_paying_and_stamps_everything(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        fund.post_premium("VHC-1", amount_minor=100_000, day=1)
        fund.activate_backstop(by="fund-board",
                               reason="insurer licence suspended",
                               notice_ref="BCV-NOTICE-2026-041", day=10)
        out = fund.spend("claims_medevac", amount_minor=20_000,
                         purpose="claim CLM-7 remittance", day=11)
        self.assertEqual(out["balance_minor"], 25_000)
        self.assertEqual(fund.entries[-1]["mode"], "backstop")
        fund.post_premium("VHC-2", amount_minor=5000, day=12)
        self.assertEqual(fund.entries[-1]["mode"], "backstop")
        rep = fund.report()
        self.assertEqual(rep["mode"], "backstop")
        self.assertEqual(rep["backstop"]["notice_ref"],
                         "BCV-NOTICE-2026-041")

    def test_exit_needs_solvency_evidence(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        fund.activate_backstop(by="board", reason="insolvency",
                               notice_ref="BCV-1", day=10)
        with self.assertRaises(FundError):
            fund.deactivate_backstop(by="board", reason="recovered",
                                     solvency_ref="", day=40)
        fund.deactivate_backstop(by="board", reason="new insurer bound",
                                 solvency_ref="BCV-SOLV-2026-09", day=40)
        self.assertEqual(fund.report()["mode"], "normal")

    def test_report_shows_all_five_buckets(self):
        fund = FundLedger(rules_version="cv-fund@1.0.0")
        fund.post_premium("VHC-1", amount_minor=5000, day=1)
        rep = fund.report()
        self.assertEqual(len(rep["balances_minor"]), 5)
        self.assertEqual(rep["allocated_minor"], 5000)


if __name__ == "__main__":
    unittest.main()
