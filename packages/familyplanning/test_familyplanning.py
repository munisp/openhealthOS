import unittest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stockledger"))
from familyplanning import FPRegister, FPError
from stockledger import Ledger


class TestFP(unittest.TestCase):
    def setUp(self):
        self.r = FPRegister()
        self.r.counsel("w1", day=100, counsellor="chw-2",
                       discussed=["implant", "injectable-depo", "pills-coc"])

    def test_provision_requires_counselling(self):
        r2 = FPRegister()
        with self.assertRaises(FPError):
            r2.provide("stranger", "implant", day=1, provider="n",
                       cadre="nurse")

    def test_counselling_must_name_methods(self):
        with self.assertRaises(FPError):
            self.r.counsel("w2", day=100, counsellor="c", discussed=[])

    def test_cadre_scope(self):
        with self.assertRaises(FPError):
            self.r.provide("w1", "implant", day=101, provider="c",
                           cadre="chw")
        rec = self.r.provide("w1", "implant", day=101, provider="n",
                             cadre="nurse")
        self.assertIsNone(rec["resupply_due_day"])

    def test_resupply_schedule_and_overdue(self):
        rec = self.r.provide("w1", "injectable-depo", day=100,
                             provider="n", cadre="nurse")
        self.assertEqual(rec["resupply_due_day"], 184)
        self.assertEqual(self.r.resupply_overdue(as_of=190), [])
        rows = self.r.resupply_overdue(as_of=200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["days_overdue"], 200 - 184 - 7)

    def test_discontinue_stops_resupply_tracking(self):
        self.r.provide("w1", "pills-coc", day=100, provider="n",
                       cadre="nurse")
        self.r.discontinue("w1", day=110, reason="desires-pregnancy")
        self.assertEqual(self.r.resupply_overdue(as_of=300), [])

    def test_stock_out_records_unmet_demand(self):
        ledger = Ledger("phc-1")  # empty shelf
        r = FPRegister(stock_ledger=ledger)
        r.counsel("w9", day=100, counsellor="c",
                  discussed=["injectable-depo"])
        with self.assertRaises(FPError) as cm:
            r.provide("w9", "injectable-depo", day=100, provider="n",
                      cadre="nurse", stock_item="depo-syringe")
        self.assertIn("stock-out", str(cm.exception))
        self.assertEqual(len(r.unmet_demand), 1)
        self.assertEqual(r.method_mix()["unmet_demand_events"], 1)

    def test_stock_link_success(self):
        ledger = Ledger("phc-1")
        ledger.receive("depo-syringe", 10, day=1, source="store")
        r = FPRegister(stock_ledger=ledger)
        r.counsel("w9", day=100, counsellor="c",
                  discussed=["injectable-depo"])
        r.provide("w9", "injectable-depo", day=100, provider="n",
                  cadre="nurse", stock_item="depo-syringe")
        self.assertEqual(ledger.balance("depo-syringe"), 9)

    def test_method_mix(self):
        self.r.provide("w1", "implant", day=101, provider="n", cadre="nurse")
        self.r.counsel("w2", day=100, counsellor="c",
                       discussed=["condom"])
        self.r.provide("w2", "condom", day=100, provider="c", cadre="chw")
        mix = self.r.method_mix()
        self.assertEqual(mix["active_methods"], {"implant": 1, "condom": 1})
        self.assertEqual(mix["clients_counselled"], 2)


if __name__ == "__main__":
    unittest.main()
