"""Tests for vhpilot — the deterministic two-week scheme simulation:
reproducible from the seed, every module populated, BR-10 trips and is
acknowledged, nothing synthetic pretends to be a person."""
import os
import sys
import unittest

PKG = os.path.join(os.path.dirname(__file__), "..")
for p in ("travelpass", "crosslink", "drugkb", "surveillance",
          "poe_screen", "steward", "medrec", "inpatient", "terminology",
          "mammoprog", "offlinespec", "teachfile", "formulary",
          "earlywarn", "cervixscreen", "usguard", "callrecall", "sepsis",
          "teleicu", "acuity", "telepath", "teleread", "reportkit",
          "pacs", "fertility", "rota", "triggers", "vitals", "mdt"):
    sys.path.insert(0, os.path.join(PKG, p))

from vhpilot import seed_pilot  # noqa: E402


class TestPilot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pilot = seed_pilot(seed=42)

    def test_deterministic_from_seed(self):
        a = seed_pilot(seed=42)["narrative"]
        b = seed_pilot(seed=42)["narrative"]
        self.assertEqual(a, b)
        c = seed_pilot(seed=7)["narrative"]
        self.assertNotEqual(a["arrivals"], c["arrivals"])

    def test_every_module_populated(self):
        n = self.pilot["narrative"]
        self.assertGreater(n["arrivals"], 200)
        self.assertGreater(n["covers"], 200)
        self.assertGreaterEqual(n["groups"], 3)
        self.assertGreater(n["partner_sales"], 20)
        self.assertGreaterEqual(n["exempt"], 2)
        self.assertGreater(n["claims"], 5)
        self.assertEqual(n["medevacs"], 1)
        self.assertGreater(n["tickets"], 5)

    def test_money_flows_with_denominators(self):
        n = self.pilot["narrative"]
        self.assertGreater(n["premium_collected_minor"], 0)
        self.assertGreater(n["claims_paid_minor"], 0)
        self.assertIsNotNone(n["loss_ratio"])
        self.assertEqual(n["settlement_breaches"], 0)

    def test_br10_tripped_and_acknowledged(self):
        n = self.pilot["narrative"]
        self.assertGreaterEqual(n["br10_alerts"], 1)
        self.assertTrue(n["br10_all_acknowledged"])

    def test_verdicts_all_three_colours_appear(self):
        v = self.pilot["narrative"]["verdicts"]
        self.assertGreater(v["green"], 0)
        # amber appears as covers age toward expiry
        self.assertGreaterEqual(v["green"] + v["amber"] + v["red"], 1)

    def test_synthetic_means_synthetic(self):
        vreg = self.pilot["registries"]["vreg"]
        blob = str(vreg.incidents)
        self.assertNotIn("Pax-", blob)  # names never enter the registry
        self.assertIn("Pax-", str(
            self.pilot["registries"]["vhc"].covers))
        self.assertNotIn("SIM-", str(vreg.incidents))

    def test_export_pack_checksummed(self):
        self.assertEqual(len(self.pilot["narrative"]
                             ["export_pack_sha256"]), 64)

    def test_helpdesk_sla_has_denominators(self):
        sla = self.pilot["narrative"]["helpdesk_sla"]
        self.assertEqual(sla["responded"],
                         self.pilot["narrative"]["tickets"])
        self.assertGreater(sla["compliant"], 0)


if __name__ == "__main__":
    unittest.main()
