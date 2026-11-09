"""Dedicated suite for referral/router.py — stale capability doctrine."""
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from router import CapabilityRegistry, route, STALE_DAYS


class TestRouter(unittest.TestCase):
    def test_fresh_attestation_routes(self):
        r = CapabilityRegistry()
        r.attest("fac-a", "ct-scan", day=10)
        out = route("ct-scan", r, today=20)
        self.assertTrue(out["ok"])
        self.assertEqual(out["facility"], "fac-a")

    def test_stale_attestation_degrades_to_unverified(self):
        r = CapabilityRegistry()
        r.attest("fac-a", "ct-scan", day=1)
        out = route("ct-scan", r, today=1 + STALE_DAYS + 1)
        self.assertFalse(out["ok"])

    def test_unknown_capability_is_honest_empty(self):
        r = CapabilityRegistry()
        out = route("pet-scan", r, today=100)
        self.assertFalse(out["ok"])

    def test_capable_lists_stale_separately(self):
        r = CapabilityRegistry()
        r.attest("fac-fresh", "dialysis", day=95)
        r.attest("fac-stale", "dialysis", day=1)
        out = r.capable("dialysis", today=100)
        self.assertEqual(out["capable"], ["fac-fresh"])
        self.assertEqual(out["unverified_stale"], ["fac-stale"])


if __name__ == "__main__":
    unittest.main()
