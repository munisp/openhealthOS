import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from vhgolive import GATES, GoLivePlan, GateError


def green_through(plan, upto):
    """Walk a plan honestly through every gate up to and including `upto`."""
    for gate in GATES[: GATES.index(upto) + 1]:
        for cid, _ in __import__("vhgolive").GATE_CHECKS[gate]:
            plan.complete_check(gate, cid, evidence_ref=f"ev/{gate}/{cid}.pdf", by="ops")
        plan.sign_off(gate, role="proposer", by="alice")
        plan.sign_off(gate, role="approver", by="bob")
        plan.flip(gate)


class TestGoLive(unittest.TestCase):
    def test_gates_walk_in_order(self):
        plan = GoLivePlan(profile="ng-vhc@1.0.0")
        for cid, _ in __import__("vhgolive").GATE_CHECKS["G1_money_rails"]:
            plan.complete_check("G1_money_rails", cid, evidence_ref="ev/x.pdf", by="ops")
        plan.sign_off("G1_money_rails", role="proposer", by="alice")
        plan.sign_off("G1_money_rails", role="approver", by="bob")
        with self.assertRaises(GateError):
            plan.flip("G1_money_rails")  # G0 not green

    def test_check_needs_evidence(self):
        plan = GoLivePlan(profile="cv-vhc@1.0.0")
        with self.assertRaises(GateError):
            plan.complete_check("G0_foundation", "profile_versioned",
                                evidence_ref="", by="ops")

    def test_four_eyes_enforced(self):
        plan = GoLivePlan(profile="ng-vhc@1.0.0")
        plan.sign_off("G0_foundation", role="proposer", by="alice")
        with self.assertRaises(GateError):
            plan.sign_off("G0_foundation", role="approver", by="alice")

    def test_flip_refused_with_missing_checks(self):
        plan = GoLivePlan(profile="ng-vhc@1.0.0")
        plan.complete_check("G0_foundation", "profile_versioned",
                            evidence_ref="ev/p.pdf", by="ops")
        plan.sign_off("G0_foundation", role="proposer", by="alice")
        plan.sign_off("G0_foundation", role="approver", by="bob")
        with self.assertRaises(GateError):
            plan.flip("G0_foundation")

    def test_full_walk_to_green(self):
        plan = GoLivePlan(profile="cv-vhc@1.0.0")
        green_through(plan, "G5_cutover")
        rep = plan.readiness()
        self.assertEqual(rep["gates_green"], 6)
        self.assertEqual(rep["checks_done"], rep["checks_total"])
        self.assertFalse(rep["live"])  # green gates, but no canary yet

    def test_canary_requires_halt_conditions_and_drill(self):
        plan = GoLivePlan(profile="ng-vhc@1.0.0")
        green_through(plan, "G5_cutover")
        with self.assertRaises(GateError):
            plan.start_canary()
        plan.declare_halt_conditions(
            ["settlement breaks > 1000 ppm for 2 consecutive days",
             "GOP p95 > 2h over a rolling day",
             "lane red-rate > 5% unexplained"], by="pm")
        with self.assertRaises(GateError):
            plan.start_canary()  # no rollback drill yet
        plan.record_rollback_drill(day=0, by="sre", note="reverted tag, lanes to manual")
        plan.start_canary()
        self.assertEqual(plan.readiness()["canary_stage"], "canary_5")

    def test_halt_freezes_ramp_and_unfreeze_is_four_eyes(self):
        plan = GoLivePlan(profile="ng-vhc@1.0.0")
        green_through(plan, "G5_cutover")
        plan.declare_halt_conditions(["GOP p95 > 2h over a rolling day"], by="pm")
        plan.record_rollback_drill(day=0, by="sre", note="drill note")
        plan.start_canary()
        plan.halt_ramp(condition="GOP p95 > 2h over a rolling day", by="duty")
        with self.assertRaises(GateError):
            plan.advance_canary()
        with self.assertRaises(GateError):
            plan.unfreeze_ramp(proposer="alice", approver="alice", note="x")
        plan.unfreeze_ramp(proposer="alice", approver="bob", note="clock green 3d")
        plan.advance_canary()
        self.assertEqual(plan.readiness()["canary_stage"], "canary_25")

    def test_undeclared_halt_condition_refused(self):
        plan = GoLivePlan(profile="cv-vhc@1.0.0")
        green_through(plan, "G5_cutover")
        plan.declare_halt_conditions(["settlement breaks > 1000 ppm"], by="pm")
        plan.record_rollback_drill(day=0, by="sre", note="drill")
        plan.start_canary()
        with self.assertRaises(GateError):
            plan.halt_ramp(condition="vibes felt off", by="duty")

    def test_ramp_to_full_live(self):
        plan = GoLivePlan(profile="ng-vhc@1.0.0")
        green_through(plan, "G5_cutover")
        plan.declare_halt_conditions(["settlement breaks > 1000 ppm"], by="pm")
        plan.record_rollback_drill(day=0, by="sre", note="drill")
        plan.start_canary()
        plan.advance_canary()  # 25
        plan.advance_canary()  # 50
        plan.advance_canary()  # full
        rep = plan.readiness()
        self.assertEqual(rep["canary_stage"], "full")
        self.assertTrue(rep["live"])

    def test_profile_must_be_pinned(self):
        with self.assertRaises(GateError):
            GoLivePlan(profile="ng-vhc")


if __name__ == "__main__":
    unittest.main()
