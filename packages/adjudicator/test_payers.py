#!/usr/bin/env python3
"""Payer network tests (ADR-0012): routing, fraud guards, idempotency."""
import unittest

from payers import (CrossPayerReplay, Payer, PayerRouter, UnknownScheme,
                    DEFAULT_PAYERS)


def claim(cid="CLM-1"):
    return {"claim_id": cid, "member_id": "NG-0001", "lines": []}


def approve(scheme, cid="CLM-1"):
    return {"claim_id": cid, "decision": "APPROVE", "scheme": scheme, "total_ngn": 5000}


class TestRouting(unittest.TestCase):
    def test_government_scheme_routes_to_nhia(self):
        r = PayerRouter()
        rec = r.submit(claim(), approve("BHCPF-VUL"))
        self.assertEqual(rec.payer_id, "NHIA")
        self.assertEqual(rec.status, "received")
        self.assertTrue(rec.reference.startswith("NHIA-SBX-"))

    def test_commercial_scheme_routes_to_hmo(self):
        r = PayerRouter()
        rec = r.submit(claim(), approve("HYGEIA-COMM"))
        self.assertEqual(rec.payer_id, "HYGEIA")
        self.assertEqual(rec.status, "received")

    def test_unknown_scheme_refused(self):
        r = PayerRouter()
        with self.assertRaises(UnknownScheme):
            r.submit(claim(), approve("FLY-BY-NIGHT-SCHEME"))
        # and nothing was recorded
        self.assertEqual(r.submissions(), [])

    def test_unapproved_decision_not_submitted(self):
        r = PayerRouter()
        rec = r.submit(claim(), {"claim_id": "CLM-1", "decision": "PEND", "scheme": "BHCPF-VUL"})
        self.assertEqual(rec.status, "error")
        self.assertEqual(r.submissions(), [])


class TestFraudGuards(unittest.TestCase):
    def test_same_payer_retry_is_idempotent(self):
        r = PayerRouter()
        first = r.submit(claim(), approve("STATE-BASIC"))
        again = r.submit(claim(), approve("STATE-BASIC"))
        self.assertEqual(again.status, "duplicate")
        self.assertEqual(again.reference, first.reference)
        self.assertEqual(len(r.submissions()), 1)

    def test_cross_payer_replay_refused(self):
        """The classic double-dip: same claim_id sent to NHIA and an HMO."""
        r = PayerRouter()
        r.submit(claim("CLM-9"), approve("BHCPF-VUL", "CLM-9"))
        with self.assertRaises(CrossPayerReplay):
            r.submit(claim("CLM-9"), approve("HYGEIA-COMM", "CLM-9"))
        # only the NHIA submission exists
        self.assertEqual(r.by_payer()["NHIA"], 1)
        self.assertEqual(r.by_payer()["HYGEIA"], 0)

    def test_scheme_cannot_be_registered_twice(self):
        dup = Payer("ACME", "Acme HMO", "commercial", ("BHCPF-VUL",))
        with self.assertRaises(ValueError):
            PayerRouter(payers=DEFAULT_PAYERS + (dup,))


class TestRuntimeRegistration(unittest.TestCase):
    def test_new_hmo_plugs_in_without_code_change(self):
        r = PayerRouter()
        r.register_payer(Payer("ACME", "Acme HMO", "commercial", ("ACME-GOLD",)))
        rec = r.submit(claim(), approve("ACME-GOLD"))
        self.assertEqual(rec.payer_id, "ACME")
        self.assertEqual(r.status("CLM-1"), "received")
        self.assertEqual(r.by_payer()["ACME"], 1)

    def test_status_unknown_for_unrouted_claim(self):
        r = PayerRouter()
        self.assertEqual(r.status("NOPE"), "unknown")


if __name__ == "__main__":
    unittest.main()
