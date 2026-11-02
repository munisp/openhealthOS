"""test_offlinespec.py — checkout is a claim; conflicts are loud and
never merged; the SLA clock does not pause for flights."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "teleread"))

from offlinespec import OfflineError, OfflineReader  # noqa: E402
from teleread import (DomainConfig, ReaderRegistry,  # noqa: E402
                      ReadingService)


def svc_and_reader():
    readers = ReaderRegistry()
    readers.register("rad-1", licence_id="L1", licence_expiry_day=400,
                     scopes=("chest",))
    readers.register("rad-2", licence_id="L2", licence_expiry_day=400,
                     scopes=("chest",))
    svc = ReadingService(
        DomainConfig("radiology", scopes=("chest",),
                     sla_hours={"routine": 24}), readers)
    svc.order("CX-1", priority="routine", question="?pneumonia",
              facility="f", referrer="mo-1", day=0, scope="chest")
    svc.order("CX-2", priority="routine", question="?effusion",
              facility="f", referrer="mo-1", day=0, scope="chest")
    return svc


class TestCheckout(unittest.TestCase):
    def test_checkout_claims_and_locks(self):
        svc = svc_and_reader()
        off = OfflineReader(svc)
        off.checkout("B1", "rad-1", ["CX-1"], day=0)
        self.assertEqual(svc.cases["CX-1"]["assigned_to"], "rad-1")
        with self.assertRaises(OfflineError):   # claimed already
            off.checkout("B2", "rad-2", ["CX-1"], day=0)

    def test_uncredentialed_and_expired_refuse(self):
        svc = svc_and_reader()
        off = OfflineReader(svc)
        with self.assertRaises(OfflineError):
            off.checkout("B1", "ghost", ["CX-1"], day=0)
        with self.assertRaises(OfflineError):
            off.checkout("B1", "rad-1", ["CX-1"], day=999)

    def test_draft_only_for_bundle_cases(self):
        svc = svc_and_reader()
        off = OfflineReader(svc)
        off.checkout("B1", "rad-1", ["CX-1"], day=0)
        with self.assertRaises(OfflineError):
            off.draft_offline("B1", "CX-2", findings="x",
                              impression="y", template_version="t@1",
                              day=0)
        off.draft_offline("B1", "CX-1", findings="clear",
                          impression="normal", template_version="t@1",
                          day=0)
        with self.assertRaises(OfflineError):   # no offline rewrite
            off.draft_offline("B1", "CX-1", findings="changed",
                              impression="x", template_version="t@1",
                              day=1)


class TestSync(unittest.TestCase):
    def test_clean_sync_lands_drafts(self):
        svc = svc_and_reader()
        off = OfflineReader(svc)
        off.checkout("B1", "rad-1", ["CX-1", "CX-2"], day=0)
        off.draft_offline("B1", "CX-1", findings="consolidation",
                          impression="pneumonia",
                          template_version="cxr@1.0.0", day=1)
        out = off.sync_back("B1", day=2)
        self.assertEqual(len(out["landed"]), 1)
        self.assertEqual(out["conflicts"], [])
        self.assertEqual(svc.reports["CX-1"]["state"], "draft")
        with self.assertRaises(OfflineError):   # no double sync
            off.sync_back("B1", day=3)

    def test_conflict_is_kept_and_flagged_never_merged(self):
        svc = svc_and_reader()
        off = OfflineReader(svc)
        off.checkout("B1", "rad-1", ["CX-1"], day=0)
        off.draft_offline("B1", "CX-1", findings="offline findings",
                          impression="offline impression",
                          template_version="cxr@1.0.0", day=1)
        # while the reader was offline, the case was escalated —
        # the bundle's snapshot no longer matches
        svc.cases["CX-1"]["priority"] = "stat"
        out = off.sync_back("B1", day=2)
        self.assertEqual(out["landed"], [])
        self.assertEqual(len(out["conflicts"]), 1)
        self.assertEqual(out["conflicts"][0]["offline_draft"]
                         ["findings"], "offline findings")
        self.assertNotIn("CX-1", svc.reports)  # never merged


if __name__ == "__main__":
    unittest.main()
