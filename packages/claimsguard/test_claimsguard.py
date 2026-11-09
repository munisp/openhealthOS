
import unittest
from claimsguard import (PayerRegistry, PreAuthGuard, GuardError,
                         EMERGENCY_SLA_HOURS, DEFAULT_SLA_HOURS)


def _guard():
    reg = PayerRegistry()
    reg.declare("NHIA", sla_hours=2.0, deemed_consent_hours=6.0,
                legal_basis="NHIA Act 2022 s.31(4)")
    reg.declare("AXA-Mansard", sla_hours=4.0)
    return PreAuthGuard(reg)


class TestClaimsGuard(unittest.TestCase):
    def test_pending_within_sla(self):
        g = _guard()
        g.submit("pa-1", payer="AXA-Mansard", procedure="appendectomy",
                 amount_minor=45000000, emergency=False, now_h=0.0,
                 requested_by="dr-a")
        self.assertEqual(g.sweep(3.0), [])  # SLA is 4h

    def test_sla_breach_escalates_exactly_once(self):
        g = _guard()
        g.submit("pa-1", payer="AXA-Mansard", procedure="x",
                 amount_minor=100, emergency=False, now_h=0.0,
                 requested_by="dr-a")
        first = g.sweep(5.0)
        self.assertEqual(first[0]["to"], "escalated")
        self.assertEqual(g.sweep(9.0), [])  # exactly once

    def test_deemed_consent_with_legal_basis(self):
        g = _guard()
        g.submit("pa-1", payer="NHIA", procedure="x", amount_minor=100,
                 emergency=False, now_h=0.0, requested_by="dr-a")
        changed = g.sweep(6.5)
        self.assertEqual(changed[0]["to"], "deemed-approved")
        self.assertIn("NHIA Act", changed[0]["legal_basis"])

    def test_deemed_consent_requires_legal_basis(self):
        reg = PayerRegistry()
        with self.assertRaises(GuardError):
            reg.declare("RogueInsurer", sla_hours=2.0,
                        deemed_consent_hours=5.0)  # no basis

    def test_denial_requires_reason(self):
        g = _guard()
        g.submit("pa-1", payer="NHIA", procedure="x", amount_minor=100,
                 emergency=False, now_h=0.0, requested_by="dr-a")
        with self.assertRaises(GuardError):
            g.decide("pa-1", approved=False, now_h=1.0, by="payer-x")

    def test_emergency_clock_is_shorter(self):
        g = _guard()
        g.submit("pa-1", payer="AXA-Mansard", procedure="laparotomy",
                 amount_minor=90000000, emergency=True, now_h=0.0,
                 requested_by="dr-a")
        changed = g.sweep(EMERGENCY_SLA_HOURS + 0.1)
        self.assertEqual(changed[0]["to"], "escalated")

    def test_unregistered_payer_gets_default_stated(self):
        g = _guard()
        self.assertEqual(g.registry.sla("unknown-payer"), DEFAULT_SLA_HOURS)

    def test_duplicate_refused(self):
        g = _guard()
        g.submit("pa-1", payer="NHIA", procedure="x", amount_minor=1,
                 emergency=False, now_h=0.0, requested_by="dr-a")
        with self.assertRaises(GuardError):
            g.submit("pa-1", payer="NHIA", procedure="y", amount_minor=1,
                     emergency=False, now_h=0.0, requested_by="dr-b")


if __name__ == "__main__":
    unittest.main()
