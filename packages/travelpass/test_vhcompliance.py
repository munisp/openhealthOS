"""Compliance tests — privacy pack, treasury FX, drill automation."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vhprivacy import (PRIVACY_REGIMES, PrivacyError, PrivacyRegister,
                       regime)
from vhtreasury import (RATES, TreasuryError, corridor_report,
                        eur_value_minor, register_rate)
from vhdrills import (DrillBook, DrillError, demo_staleness)


class TestPrivacy(unittest.TestCase):
    def setUp(self):
        self.reg = PrivacyRegister()

    def test_regimes_are_entries(self):
        self.assertEqual(regime("ng")["dpa"], "NDPC")
        self.assertEqual(regime("cv")["dpa"], "CNPD")
        with self.assertRaises(PrivacyError):
            regime("xx")

    def test_erasure_is_four_eyes_and_reasoned(self):
        with self.assertRaises(PrivacyError):
            self.reg.erase(country="ng", pseudonym="P1",
                           salt_mapping_revoked=True,
                           requested_by="ada", approved_by="ada",
                           day=1, reason="subject request")
        with self.assertRaises(PrivacyError):
            self.reg.erase(country="ng", pseudonym="P1",
                           salt_mapping_revoked=True,
                           requested_by="ada", approved_by="bola",
                           day=1, reason="")

    def test_erasure_requires_mapping_revocation(self):
        with self.assertRaises(PrivacyError):
            self.reg.erase(country="ng", pseudonym="P1",
                           salt_mapping_revoked=False,
                           requested_by="ada", approved_by="bola",
                           day=1, reason="subject request")

    def test_erasure_tombstones_and_refuses_twice(self):
        self.reg.erase(country="ng", pseudonym="P1",
                       salt_mapping_revoked=True,
                       requested_by="ada", approved_by="bola",
                       day=1, reason="subject request")
        self.assertTrue(self.reg.is_erased("P1"))
        with self.assertRaises(PrivacyError):
            self.reg.erase(country="ng", pseudonym="P1",
                           salt_mapping_revoked=True,
                           requested_by="ada", approved_by="bola",
                           day=2, reason="again")

    def test_audit_pack_is_stamped(self):
        self.reg.erase(country="ng", pseudonym="P1",
                       salt_mapping_revoked=True,
                       requested_by="ada", approved_by="bola",
                       day=1, reason="subject request")
        pack = self.reg.audit_pack(country="ng", day=2)
        self.assertEqual(pack["dpa"], "NDPC")
        self.assertEqual(len(pack["sha256"]), 64)
        self.assertEqual(len(pack["erasures"]), 1)


class TestTreasury(unittest.TestCase):
    def test_peg_revaluation(self):
        # 2,500 EUR cents at the BCV peg == itself; CVE 1102650 minor
        # (11,026.50 escudos at scale 100) == 100.00 EUR
        self.assertEqual(eur_value_minor("EUR", 2500), 2500)
        self.assertEqual(eur_value_minor("CVE", 1102650), 10000)

    def test_unknown_rate_refuses(self):
        with self.assertRaises(TreasuryError):
            eur_value_minor("GBP", 1000)   # valid currency, no stated rate

    def test_register_rate_validates(self):
        with self.assertRaises(TreasuryError):
            register_rate("NGN", per_eur=-1, kind="x", source="s", day=0)
        register_rate("ETB", per_eur=120.0, kind="indicative",
                      source="operator", day=1)
        self.assertIn("ETB", RATES)

    def test_corridor_report_groups_and_stamps_unpriced(self):
        rows = corridor_report([
            {"payer": "A", "amount_minor": 100, "currency": "NGN",
             "ref": "1", "corridor": "LOS-ABV"},
            {"payer": "B", "amount_minor": 200, "currency": "NGN",
             "ref": "2", "corridor": "LOS-ABV"},
            {"payer": "C", "amount_minor": 50, "currency": "GBP",
             "ref": "3", "corridor": "ADD-LOS"}])
        ng = [r for r in rows if r["currency"] == "NGN"][0]
        self.assertEqual(ng["minor"], 300)
        self.assertEqual(ng["lines"], 2)
        gbp = [r for r in rows if r["currency"] == "GBP"][0]
        # GBP has no stated rate: exposure is stated unpriced, not zero
        self.assertIsNone(gbp["eur_minor"])
        self.assertEqual(gbp["rate_kind"], "unpriced")


class TestDrills(unittest.TestCase):
    def setUp(self):
        self.book = DrillBook(freshness_days=30)

    def test_undeclared_scenario_refuses(self):
        with self.assertRaises(DrillError):
            self.book.run_drill("alien_abduction", day=0,
                                recovered_minutes=1,
                                evidence="log", run_by="ops")

    def test_drill_artifact_is_stamped(self):
        a = self.book.run_drill("rollback", day=0, recovered_minutes=10,
                                evidence="snapshot-restore.log",
                                run_by="ops-lead")
        self.assertTrue(a["within_budget"])
        self.assertEqual(len(a["sha256"]), 64)

    def test_freshness_and_staleness(self):
        self.book.run_drill("rollback", day=0, recovered_minutes=10,
                            evidence="e", run_by="ops")
        fresh = self.book.freshness(day=10)
        self.assertEqual(fresh["rollback"]["status"], "fresh")
        self.assertEqual(fresh["canary_halt"]["status"], "never_run")
        stale = self.book.freshness(day=100)
        self.assertEqual(stale["rollback"]["status"], "stale")
        self.assertFalse(self.book.gate_evidence_ok(day=100))

    def test_over_budget_drill_is_not_fresh_evidence(self):
        self.book.run_drill("rollback", day=0, recovered_minutes=90,
                            evidence="e", run_by="ops")
        self.assertEqual(self.book.freshness(day=1)["rollback"]
                         ["status"], "never_run")

    def test_demo_staleness_names_drift(self):
        stale = demo_staleness({"fund": {"sha256": "aaa"}},
                               {"fund": {"sha256": "bbb"}})
        self.assertEqual(stale[0]["payload"], "fund")
        stale2 = demo_staleness({"fund": {"sha256": "aaa"}}, {})
        self.assertIn("missing", stale2[0]["reason"])
        self.assertEqual(demo_staleness({"fund": {"sha256": "aaa"}},
                                        {"fund": {"sha256": "aaa"}}), [])


if __name__ == "__main__":
    unittest.main()
