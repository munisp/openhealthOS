"""Tests for vhrescue — medevac orchestration with a dispatch SLA,
repatriation of remains, the uninsured-emergency fund ledger, and
subrogation with money that lands back in the fund."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhrescue import (RescueError, RescueRegistry,  # noqa: E402
                      SubrogationRegistry, UninsuredFund)


class TestMedevac(unittest.TestCase):
    def _requested(self, reg, now_h=100):
        return reg.request_medevac(
            "CLM-1", from_facility="HOSP-SAL", to_destination="HOSP-PRAIA",
            clinical_justification="head injury, neurosurgery needed",
            requested_by="dr-1", now_h=now_h)

    def test_full_mission_within_sla(self):
        reg = RescueRegistry(rules_version="vhc-rescue@1.0.0")
        m = self._requested(reg)
        reg.approve(m["mission_id"], by="medical-director",
                    note="neurosurgical emergency", now_h=101)
        d = reg.dispatch(m["mission_id"], asset="fixed-wing", now_h=102)
        self.assertFalse(d["breaches"])
        reg.arrive(m["mission_id"], now_h=104)
        reg.close(m["mission_id"], outcome="patient handed over",
                  now_h=105)
        self.assertEqual(m["state"], "closed")

    def test_no_justification_is_a_taxi_ride(self):
        reg = RescueRegistry(rules_version="vhc-rescue@1.0.0")
        with self.assertRaises(RescueError):
            reg.request_medevac("CLM-1", from_facility="A",
                                to_destination="B",
                                clinical_justification="",
                                requested_by="dr-1", now_h=0)

    def test_unsigned_approval_refuses(self):
        reg = RescueRegistry(rules_version="vhc-rescue@1.0.0")
        m = self._requested(reg)
        with self.assertRaises(RescueError):
            reg.approve(m["mission_id"], by="", note="", now_h=101)

    def test_unapproved_dispatch_refuses(self):
        reg = RescueRegistry(rules_version="vhc-rescue@1.0.0")
        m = self._requested(reg)
        with self.assertRaises(RescueError):
            reg.dispatch(m["mission_id"], asset="boat", now_h=101)

    def test_late_dispatch_is_a_named_breach(self):
        reg = RescueRegistry(rules_version="vhc-rescue@1.0.0")
        m = self._requested(reg)
        reg.approve(m["mission_id"], by="md", note="ok", now_h=101)
        reg.dispatch(m["mission_id"], asset="rotary", now_h=105)
        rep = reg.report()
        self.assertEqual(rep["dispatch_sla"],
                         {"compliant": 0, "total": 1})

    def test_unknown_asset_refuses(self):
        reg = RescueRegistry(rules_version="vhc-rescue@1.0.0")
        m = self._requested(reg)
        reg.approve(m["mission_id"], by="md", note="ok", now_h=101)
        with self.assertRaises(RescueError):
            reg.dispatch(m["mission_id"], asset="catapult", now_h=102)

    def test_remains_need_mortuary_and_consulate(self):
        reg = RescueRegistry(rules_version="vhc-rescue@1.0.0")
        with self.assertRaises(RescueError):
            reg.repatriate_remains("CLM-9", mortuary_ref="MOR-1",
                                   consulate_ref="", day=10, note="n")
        m = reg.repatriate_remains("CLM-9", mortuary_ref="MOR-1",
                                   consulate_ref="CON-DE-1", day=10,
                                   note="deceased after ED arrival")
        self.assertEqual(m["kind"], "repatriation-of-remains")
        self.assertEqual(reg.report()["repatriations"], 1)


class TestUninsuredFund(unittest.TestCase):
    def test_post_and_partial_recovery(self):
        fund = UninsuredFund()
        e = fund.post("CLM-UN-1", provider_ref="HOSP-1",
                      amount_minor=50_000, day=10,
                      reason="lapsed cover, airway first")
        r = fund.recover(e["entry_id"], amount_minor=20_000, day=20,
                         via="embassy-repayment")
        self.assertEqual(r["outstanding_minor"], 30_000)
        rep = fund.report()
        self.assertEqual(rep["posted_minor"], 50_000)
        self.assertEqual(rep["outstanding_minor"], 30_000)

    def test_over_recovery_is_creative_accounting(self):
        fund = UninsuredFund()
        e = fund.post("C", provider_ref="H", amount_minor=100, day=1,
                      reason="r")
        with self.assertRaises(RescueError):
            fund.recover(e["entry_id"], amount_minor=101, day=2,
                         via="x")

    def test_silent_absorption_refuses(self):
        fund = UninsuredFund()
        with self.assertRaises(RescueError):
            fund.post("C", provider_ref="", amount_minor=100, day=1,
                      reason="")


class TestSubrogation(unittest.TestCase):
    def test_full_lifecycle_recovers_to_fund(self):
        fund = UninsuredFund()
        reg = SubrogationRegistry(fund=fund)
        sub = reg.identify("CLM-1", third_party="Hotel Azul",
                           basis="pool negligence, premises liability",
                           day=10)
        reg.demand(sub["sub_id"], amount_minor=75_000, day=12)
        r = reg.recover(sub["sub_id"], amount_minor=75_000, day=30)
        self.assertIsNotNone(r["fund_entry"])
        self.assertEqual(fund.report()["posted_minor"], 75_000)
        self.assertEqual(reg.claims[sub["sub_id"]]["state"], "recovered")

    def test_identify_without_basis_refuses(self):
        reg = SubrogationRegistry()
        with self.assertRaises(RescueError):
            reg.identify("CLM-1", third_party="Hotel", basis="", day=1)

    def test_recovery_bounded_by_demand(self):
        reg = SubrogationRegistry()
        sub = reg.identify("C", third_party="T", basis="b", day=1)
        reg.demand(sub["sub_id"], amount_minor=100, day=2)
        with self.assertRaises(RescueError):
            reg.recover(sub["sub_id"], amount_minor=150, day=3)

    def test_write_off_needs_approver_and_reason(self):
        reg = SubrogationRegistry()
        sub = reg.identify("C", third_party="T", basis="b", day=1)
        with self.assertRaises(RescueError):
            reg.write_off(sub["sub_id"], by="", reason="", day=5)
        reg.write_off(sub["sub_id"], by="cfo", reason="debtor insolvent",
                      day=5)
        self.assertEqual(reg.report()["by_state"]["written-off"], 1)


if __name__ == "__main__":
    unittest.main()
