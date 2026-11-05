"""Tests for vhease — the composed EASE rail: no transport is no
interface, idempotency keys on everything, failures refuse loudly."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhease import EaseAdapter, EaseError  # noqa: E402


def _ok_transport(msg_type, envelope):
    return {"status": "accepted", "rail_ref": "EASE-1"}


def _cover():
    return {"ref": "VHC-1", "passport_sha256": "x" * 64,
            "window": {"from": 10, "to": 39},
            "profile_version": "cv-vhc@1.0.0"}


class TestRail(unittest.TestCase):
    def test_no_transport_is_no_interface(self):
        with self.assertRaises(EaseError):
            EaseAdapter(None)

    def test_cover_issued_flows_with_idem_key(self):
        rail = EaseAdapter(_ok_transport)
        ack = rail.cover_issued(_cover())
        self.assertEqual(ack["status"], "accepted")
        env = rail.sent[-1]
        self.assertEqual(env["type"], "cover-issued")
        self.assertEqual(len(env["idem_key"]), 64)

    def test_idem_keys_are_stable_and_distinct(self):
        rail = EaseAdapter(_ok_transport)
        rail.cover_issued(_cover())
        rail.cover_issued(_cover())
        self.assertEqual(rail.sent[0]["idem_key"], rail.sent[1]
                         ["idem_key"])  # same payload, same key
        rail.cover_revoked("VHC-1", reason="fraud", day=5)
        self.assertNotEqual(rail.sent[0]["idem_key"],
                            rail.sent[2]["idem_key"])

    def test_rail_failure_refuses_loudly(self):
        def boom(t, e):
            raise ConnectionError("rail down")
        rail = EaseAdapter(boom)
        with self.assertRaises(EaseError) as ctx:
            rail.cover_issued(_cover())
        self.assertIn("refused loudly", str(ctx.exception))

    def test_non_acceptance_refuses(self):
        rail = EaseAdapter(lambda t, e: {"status": "busy"})
        with self.assertRaises(EaseError):
            rail.cover_issued(_cover())

    def test_verification_event_rides_back(self):
        rail = EaseAdapter(_ok_transport)
        verdict = {"ref": "VHC-1", "verdict": "green", "reasons": [],
                   "evidence": {"mode": "offline", "latency_ms": 3.1}}
        rail.verification_event(verdict, lane="sal-airport-3", day=12)
        self.assertEqual(rail.sent[-1]["payload"]["verdict"], "green")

    def test_unknown_message_type_refuses(self):
        rail = EaseAdapter(_ok_transport)
        with self.assertRaises(EaseError):
            rail._send("smuggle", {})

    def test_report_counts_by_type(self):
        rail = EaseAdapter(_ok_transport)
        rail.cover_issued(_cover())
        rail.cover_revoked("VHC-1", reason="r", day=1)
        rep = rail.report()
        self.assertEqual(rep["messages_sent"], 2)
        self.assertEqual(rep["by_type"]["cover-issued"], 1)


if __name__ == "__main__":
    unittest.main()
