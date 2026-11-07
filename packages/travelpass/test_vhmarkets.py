"""Market layer tests — vhmarkets, vhoperators, template profiles."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vhmarkets import (CURRENCIES, MARKETS, MarketsError, currency,
                       fmt_major, fmt_minor, market, market_currency,
                       report as mreport)
from vhoperators import OperatorRegistry, OperatorError
from vhcover import VHC_PROFILES, price_cover, VHCError


class TestMarkets(unittest.TestCase):
    def test_africa_and_archipelago_are_entries(self):
        for c in ("ng", "cv", "gh", "ke", "za", "sn", "ci", "tz", "rw",
                  "eg", "ma", "et", "cm", "mz", "ao"):
            self.assertIn(c, MARKETS)

    def test_currency_minor_names(self):
        self.assertEqual(fmt_minor("NGN", 550000), "550,000 kobo")
        self.assertEqual(fmt_minor("CVE", 2500), "2,500 centavos")
        self.assertEqual(fmt_major("NGN", 550000), "₦5,500.00")
        self.assertEqual(fmt_major("XOF", 15000), "CFA15,000")  # scale 1

    def test_unknown_market_refuses(self):
        with self.assertRaises(MarketsError):
            market("xx")
        with self.assertRaises(MarketsError):
            currency("ZZZ")

    def test_dpa_gaps_stated_not_invented(self):
        self.assertIsNone(market("et")["dpa"])
        self.assertIsNotNone(market("ng")["dpa"])
        self.assertIn("et", mreport()["markets_without_dpa"])


class TestTemplateProfiles(unittest.TestCase):
    def test_template_profiles_present(self):
        for pid in ("gh-vhc@0.9.0", "ke-vhc@0.9.0", "za-vhc@0.9.0",
                    "sn-vhc@0.9.0"):
            self.assertEqual(VHC_PROFILES[pid]["status"], "template")

    def test_template_refuses_to_price(self):
        with self.assertRaises(VHCError):
            price_cover(VHC_PROFILES["gh-vhc@0.9.0"], days=5, age_years=30)

    def test_ratified_profiles_still_price(self):
        total, _ = price_cover(VHC_PROFILES["ng-vhc@1.0.0"], days=14,
                               age_years=30)
        self.assertEqual(total, 1200000)
        total, units = price_cover(VHC_PROFILES["cv-vhc@1.0.0"], days=14,
                                   age_years=30)
        self.assertEqual((total, units), (2500, 1))


class TestOperators(unittest.TestCase):
    def setUp(self):
        self.reg = OperatorRegistry()
        self.reg.register("op-acme", legal_name="Acme Assurance Ltd",
                          country="ng", settlement_currency="NGN",
                          allowed_profiles=("ng-vhc@1.0.0",),
                          pricing_bounds={"ng-vhc@1.0.0":
                                          {"floor_minor": 100000,
                                           "ceiling_minor": 2000000}},
                          day=0)

    def test_unknown_operator_refuses(self):
        with self.assertRaises(OperatorError):
            self.reg.assert_sellable("nobody", "ng-vhc@1.0.0")

    def test_unlicensed_profile_refuses(self):
        with self.assertRaises(OperatorError):
            self.reg.assert_sellable("op-acme", "cv-vhc@1.0.0")

    def test_price_ratification_is_four_eyes(self):
        with self.assertRaises(OperatorError):
            self.reg.ratify_price("op-acme", "ng-vhc@1.0.0",
                                  price_minor=500000,
                                  proposed_by="ada", approved_by="ada",
                                  day=1)

    def test_price_outside_bounds_refuses(self):
        with self.assertRaises(OperatorError):
            self.reg.ratify_price("op-acme", "ng-vhc@1.0.0",
                                  price_minor=50,   # below floor
                                  proposed_by="ada", approved_by="bola",
                                  day=1)

    def test_ratified_price_reads_back(self):
        self.reg.ratify_price("op-acme", "ng-vhc@1.0.0",
                              price_minor=500000,
                              proposed_by="ada", approved_by="bola", day=1)
        self.assertEqual(self.reg.price_for("op-acme", "ng-vhc@1.0.0"),
                         500000)

    def test_unratified_price_refuses(self):
        with self.assertRaises(OperatorError):
            self.reg.price_for("op-acme", "ng-vhc@1.0.0")

    def test_register_validates_market_and_currency(self):
        with self.assertRaises(MarketsError):
            self.reg.register("op-x", legal_name="X", country="xx",
                              settlement_currency="NGN",
                              allowed_profiles=("ng-vhc@1.0.0",), day=0)
        with self.assertRaises(MarketsError):
            self.reg.register("op-y", legal_name="Y", country="ng",
                              settlement_currency="ZZZ",
                              allowed_profiles=("ng-vhc@1.0.0",), day=0)

    def test_audit_digest_stable(self):
        d1 = self.reg.audit_digest()
        self.assertEqual(d1, self.reg.audit_digest())


if __name__ == "__main__":
    unittest.main()
