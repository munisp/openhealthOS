"""Tests for devicelink.py — spool, dedupe, lifecycle, feed health."""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from devicehub import DeviceHubError, parse_oru  # noqa: E402
from devicelink import FeedHealth, ResultStore, Spool  # noqa: E402


def _parsed(mid="MSG-1", value="5.2", status="F", ts="20261103082500"):
    msg = (f"MSH|^~\\&|A|B|C|D|2026||ORU^R01|{mid}|P|2.5\r"
           "PID|1||P-1\r"
           f"OBX|1|NM|K^Potassium||{value}|mmol/L||N|||{status}|||{ts}\r")
    return parse_oru(msg)


class TestSpool(unittest.TestCase):
    def test_enqueue_pending_replay(self):
        spool = Spool()
        spool.enqueue("DH-1", "outbound", {"x": 1})
        spool.enqueue("DH-1", "outbound", {"x": 2})
        self.assertEqual(len(spool.pending("DH-1")), 2)
        delivered = []
        out = spool.replay("DH-1", delivered.append)
        self.assertEqual(out["replayed"], 2)
        self.assertEqual(len(spool.pending()), 0)
        self.assertEqual([d["x"] for d in delivered], [1, 2])

    def test_failures_stay_queued_then_dead_letter(self):
        spool = Spool()
        spool.enqueue("DH-1", "outbound", {"x": 1})

        def always_fails(payload):
            raise ConnectionError("link down")

        for _ in range(3):
            out = spool.replay("DH-1", always_fails)
        self.assertEqual(out["dead_total"], 1)
        self.assertEqual(spool.dead_letters()[0]["dead_reason"],
                         "link down")
        self.assertEqual(len(spool.pending()), 0)


class TestResultStore(unittest.TestCase):
    def test_post_and_latest(self):
        store = ResultStore()
        out = store.post(_parsed())
        self.assertEqual(out["verdict"], "posted")
        self.assertEqual(store.latest("P-1", "K")["value"], "5.2")

    def test_duplicate_acknowledged_never_double_posted(self):
        store = ResultStore()
        store.post(_parsed())
        dup = store.post(_parsed())
        self.assertEqual(dup["verdict"], "duplicate")
        self.assertEqual(store.posted_total, 1)

    def test_correction_supersedes_chain_stays(self):
        store = ResultStore()
        store.post(_parsed("MSG-1", "5.2", "F"))
        out = store.post(_parsed("MSG-2", "4.9", "C"))
        self.assertIn("superseded", out["observations"])
        self.assertEqual(store.latest("P-1", "K")["value"], "4.9")
        hist = store.history("P-1", "K")
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[1]["supersedes"], "MSG-1")

    def test_cancelled_posts_nothing(self):
        store = ResultStore()
        out = store.post(_parsed("MSG-9", "5.2", "X"))
        self.assertEqual(out["verdict"], "cancelled")
        self.assertIsNone(store.latest("P-1", "K"))

    def test_missing_message_id_refuses(self):
        store = ResultStore()
        with self.assertRaises(DeviceHubError):
            store.post({"observations": []})


class TestFeedHealth(unittest.TestCase):
    def setUp(self):
        self.fh = FeedHealth()
        self.t0 = datetime.datetime(2026, 1, 1, 8, 0, 0)

    def test_stale_detection_and_unmonitored_stated(self):
        self.fh.seen("DH-1", self.t0)
        self.fh.set_expectation("DH-1", 300)
        self.fh.seen("DH-2", self.t0)  # no expectation set
        rep = self.fh.report(self.t0 + datetime.timedelta(seconds=400))
        self.assertEqual(rep["stale"], ["DH-1"])
        states = {r["session_id"]: r["state"] for r in rep["feeds"]}
        self.assertEqual(states["DH-2"], "unmonitored")

    def test_garbage_feed_auto_quarantines_and_refuses(self):
        for _ in range(3):
            self.fh.note_parse_failure("DH-1")
        with self.assertRaises(DeviceHubError) as cm:
            self.fh.check("DH-1")
        self.assertIn("quarantined", str(cm.exception))

    def test_quarantine_clears_only_with_who_and_why(self):
        for _ in range(3):
            self.fh.note_parse_failure("DH-1")
        with self.assertRaises(DeviceHubError):
            self.fh.clear_quarantine("DH-1", by="", reason="")
        out = self.fh.clear_quarantine("DH-1", by="biomed-1",
                                       reason="replaced probe cable")
        self.assertEqual(out["cleared_by"], "biomed-1")
        self.fh.check("DH-1")  # no longer raises

    def test_clearing_absent_quarantine_refuses(self):
        with self.assertRaises(DeviceHubError):
            self.fh.clear_quarantine("DH-9", by="x", reason="y")


if __name__ == "__main__":
    unittest.main()
