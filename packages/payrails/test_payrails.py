#!/usr/bin/env python3
"""Payment rails: idempotent retries, cross-rail replay refused, live shapes."""
import io
import json
import unittest
from unittest.mock import patch

from payrails import (CrossRailReplay, MojaloopRail, NIPRail, PaymentRequest,
                      RailRouter)


def req(tx="TX-1", amount=5000, **kw):
    return PaymentRequest(tx, "FAC-1", amount, "claim payout",
                          kw.get("bank_code", ""), kw.get("account", ""))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestRails(unittest.TestCase):
    def test_sandbox_is_deterministic_and_idempotent(self):
        router = RailRouter()
        first = router.transfer(req())
        retry = router.transfer(req())
        self.assertEqual(first.status, "COMPLETED")
        self.assertEqual(retry.status, "DUPLICATE")
        self.assertEqual(first.reference, retry.reference)

    def test_cross_rail_replay_refused(self):
        router = RailRouter()
        router.transfer(req(), rail="mojaloop")
        with self.assertRaises(CrossRailReplay):
            router.transfer(req(), rail="nip")

    def test_nip_requires_account_details(self):
        router = RailRouter()
        rec = router.transfer(req(tx="TX-N"), rail="nip")
        self.assertEqual(rec.status, "ERROR")
        self.assertIn("bank_code", rec.detail)

    def test_nip_sandbox_with_details(self):
        router = RailRouter()
        rec = router.transfer(req(tx="TX-N2", bank_code="058", account="0123456789"), rail="nip")
        self.assertEqual(rec.status, "COMPLETED")
        self.assertTrue(rec.reference.startswith("NIP-SBX-"))
        self.assertEqual(router.status("TX-N2"), "COMPLETED")

    def test_mojaloop_live_posts_quote_then_transfer(self):
        calls = []

        def fake_urlopen(request, timeout=0):
            calls.append((request.full_url, request.data))
            if request.full_url.endswith("/quotes"):
                return FakeResponse({"quoteId": "QUOTE-9"})
            return FakeResponse({"transferId": "TRANSFER-9", "status": "COMPLETED"})

        rail = MojaloopRail("http://switch:9090", sandbox=False)
        with patch("urllib.request.urlopen", fake_urlopen):
            rec = rail.transfer(req(tx="TX-L"))
        self.assertEqual(rec.reference, "TRANSFER-9")
        self.assertEqual([c[0].split("/")[-1] for c in calls], ["quotes", "transfers"])
        transfer_body = json.loads(calls[1][1])
        self.assertIn("ilpPacket", transfer_body)
        self.assertEqual(transfer_body["quoteId"], "QUOTE-9")


if __name__ == "__main__":
    unittest.main()
