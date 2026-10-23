"""E2E composition + pack governance lifecycle."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
for pkg in ("protocolpack", "protocolpack/packs"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from day_in_life import run_day  # noqa: E402
from packreview import PackRegistry  # noqa: E402
from packs.ng_standing_orders import PACKS  # noqa: E402

KEY = b"lga-shared-hmac-key-32-bytes-00000"


class TestDayInLife(unittest.TestCase):
    def test_full_flow_composes(self):
        log = run_day()  # every assertion inside is the test
        self.assertEqual(log["pack_status"], "live")
        self.assertEqual(log["referral"]["facility"], "OHF-LGA-GENERAL")
        self.assertEqual(log["shepherd"], {"drafted": 2, "confirmed": 1})
        self.assertEqual(log["queue_first"], "pseudo-0042")


class TestPackGovernance(unittest.TestCase):
    def test_unreviewed_pack_refused_in_production(self):
        reg = PackRegistry(KEY)
        reg.register("fever", PACKS["fever"], author="a", now=1)
        with self.assertRaises(PermissionError):
            reg.session("fever", "chw")               # draft: no sessions
        s = reg.session("fever", "chw", allow_seed=True)  # training only
        self.assertEqual(s.pack_status, "draft")      # status on the record

    def test_author_cannot_self_approve_and_note_required(self):
        reg = PackRegistry(KEY)
        reg.register("fever", PACKS["fever"], author="dr-a", now=1)
        self.assertEqual(reg.review("fever", "dr-a", "consultant", True, "ok")["reason"],
                         "author-cannot-self-approve")
        self.assertEqual(reg.review("fever", "dr-b", "consultant", True, "")["reason"],
                         "note-required")
        self.assertEqual(reg.review("fever", "chw-x", "chw", True, "fine")["reason"],
                         "insufficient-reviewer-role")

    def test_rejected_pack_unusable_and_edit_returns_to_draft(self):
        reg = PackRegistry(KEY)
        reg.register("fever", PACKS["fever"], author="a", now=1)
        reg.review("fever", "dr-b", "consultant", False, "needs hematology step", now=2)
        with self.assertRaises(PermissionError):
            reg.session("fever", "chw", allow_seed=True)  # rejected is rejected
        # approve, then edit -> back to draft, honestly logged
        reg.register("htn", PACKS["hypertension-followup"], author="a", now=1)
        reg.review("htn", "dr-b", "consultant", True, "approved", now=2)
        edited = dict(PACKS["hypertension-followup"], version="tampered-2")
        reg.register("htn", edited, author="a", now=3)
        self.assertEqual(reg.packs["htn"]["status"], "draft")
        self.assertIn("edit-after-signoff",
                      [h["event"] for h in reg.packs["htn"]["history"]])


if __name__ == "__main__":
    unittest.main()
