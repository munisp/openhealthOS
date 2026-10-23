"""Gap-closer suites: protocolpack, telesupervision, queueintel,
referral router, chronic care panel."""
import hashlib
import hmac
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("protocolpack", "telesupervision", "queueintel", "referral", "chronicreg"):
    sys.path.insert(0, os.path.join(ROOT, pkg))
sys.path.insert(0, os.path.join(ROOT, "protocolpack", "packs"))

from protocolpack import Session, ScopeError  # noqa: E402
from packs.ng_standing_orders import PACKS  # noqa: E402
from telesupervision import CaseBundle, ReviewBoard  # noqa: E402
from queueintel import Queue  # noqa: E402
from router import CapabilityRegistry, route  # noqa: E402
from carepanel import panel  # noqa: E402

KEY = b"test-key-32-bytes-for-hmac-signing!"
H = 3600 * 1000


class TestProtocolPack(unittest.TestCase):
    def test_chw_runs_fever_protocol_to_allowed_treatment(self):
        s = Session(PACKS["fever"], "chw")
        s.answer("2-7 days")
        s.answer("no")
        st = s.answer("positive")
        self.assertTrue(st["done"])
        self.assertFalse(st["escalate"])
        self.assertEqual(st["outcome"], "chw-treat-protocol-act")
        self.assertEqual(s.perform("give-act"), {"ok": True, "action": "give-act"})
        self.assertEqual(st["pack_version"], "ngso-fever-1.0.0-seed")

    def test_red_flag_escalates_immediately(self):
        s = Session(PACKS["fever"], "chw")
        s.answer("<2 days")
        st = s.answer("yes")  # danger sign
        self.assertTrue(st["escalate"])
        self.assertEqual(st["reason"], "red-flag: yes")

    def test_scope_boundary_raises_and_records(self):
        s = Session(PACKS["fever"], "chw")
        with self.assertRaises(ScopeError):
            s.perform("adjust-antihypertensive")
        self.assertTrue(s.escalate)
        self.assertIn("scope-boundary", s.escalation_reason)
        self.assertIn(("ACTION-DENIED", "adjust-antihypertensive"), s.path)
        # a doctor is not blocked
        s2 = Session(PACKS["fever"], "doctor")
        self.assertEqual(s2.perform("adjust-antihypertensive")["ok"], True)

    def test_unversioned_pack_refused(self):
        with self.assertRaises(ValueError):
            Session({"steps": {}, "start": "x"}, "nurse")


class TestTeleSupervision(unittest.TestCase):
    def test_signed_bundle_round_trip_and_review(self):
        b = CaseBundle.create("chw-1", "pseudo-001",
                              {"protocol_outcome": {"escalate": True},
                               "vitals": {"temp": 39.2}}, KEY, now=1)
        self.assertTrue(CaseBundle.verify(dict(b), KEY))
        rb = ReviewBoard(KEY)
        self.assertTrue(rb.submit(b))
        q = rb.queue()
        self.assertEqual(len(q["escalations"]), 1)
        r = rb.review(b["sig"], "dr-okafor", "take-over", "see at 9am", KEY, now=2)
        self.assertTrue(r["ok"])

    def test_tampered_bundle_quarantined_and_unreviewable(self):
        b = CaseBundle.create("chw-1", "pseudo-001", {"vitals": {}}, KEY)
        b["content"]["vitals"]["temp"] = 41.0  # tamper after signing
        rb = ReviewBoard(KEY)
        self.assertFalse(rb.submit(b))
        self.assertEqual(len(rb.queue()["quarantined"]), 1)
        self.assertEqual(rb.review(b["sig"], "dr", "approve", "x", KEY)["reason"],
                         "quarantined-cannot-review")

    def test_double_review_refused(self):
        b = CaseBundle.create("c", "p", {"vitals": {}}, KEY)
        rb = ReviewBoard(KEY)
        rb.submit(b)
        rb.review(b["sig"], "d1", "approve", "ok", KEY)
        self.assertEqual(rb.review(b["sig"], "d2", "modify", "x", KEY)["reason"],
                         "not-found-or-reviewed")


class TestQueueIntel(unittest.TestCase):
    def test_clinical_need_before_arrival(self):
        q = Queue()
        q.arrive("r1", "routine", 0)
        q.arrive("v1", "visit", 1)
        q.arrive("e1", "emergency", 2)
        self.assertEqual(q.serve(3)["patient_ref"], "e1")
        self.assertEqual(q.serve(4)["patient_ref"], "v1")

    def test_starvation_promotion_is_documented(self):
        q = Queue()
        q.arrive("r1", "routine", 0)
        q.arrive("v1", "visit", 0)
        nxt = q.next(4 * H)  # 4h later
        self.assertEqual(nxt["patient_ref"], "r1")  # promoted past visit
        self.assertTrue(nxt["promoted"])

    def test_unknown_class_refused(self):
        with self.assertRaises(ValueError):
            Queue().arrive("x", "vip", 0)


class TestReferralRouter(unittest.TestCase):
    def test_exact_match_with_staleness_honesty(self):
        reg = CapabilityRegistry()
        reg.attest("OHF-A", "ct-scan", 10)
        reg.attest("OHF-B", "ct-scan", 10 - 200)  # 200 days stale
        r = route("ct-scan", reg, today=10, fallback="state-general")
        self.assertEqual(r["facility"], "OHF-A")
        self.assertEqual(r["stale_noted"], ["OHF-B"])

    def test_no_capability_is_honest(self):
        r = route("cathlab", CapabilityRegistry(), today=1,
                  fallback="national-teaching")
        self.assertFalse(r["ok"])
        self.assertEqual(r["fallback"], "national-teaching")


class TestCarePanel(unittest.TestCase):
    def test_exceptions_ranked_with_reasons_stable_invisible(self):
        pts = [
            {"ref": "p-stable", "last_visit_day": 3, "refill_due_day": 40, "condition": "htn"},
            {"ref": "p-missed", "last_visit_day": 1, "refill_due_day": 40, "condition": "dm"},
            {"ref": "p-both", "last_visit_day": 0, "refill_due_day": 8, "condition": "htn"},
        ]
        rows = panel(pts, today_day=10)
        self.assertEqual([r["patient_ref"] for r in rows], ["p-both", "p-missed"])
        self.assertEqual(len(rows[0]["reasons"]), 2)
        self.assertNotIn("p-stable", [r["patient_ref"] for r in rows])


if __name__ == "__main__":
    unittest.main()
