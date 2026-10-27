import unittest
from crvs import RegistrationBridge, CRVSError, QUEUE_STALE_DAYS


def _birth(eid, day=100):
    return {"event_id": eid, "event_day": day, "place": "phc-1",
            "sex": "F", "mother_id": "m-9"}


class TestCRVS(unittest.TestCase):
    def setUp(self):
        self.b = RegistrationBridge()

    def test_minimum_fields_fail_closed(self):
        with self.assertRaises(CRVSError) as cm:
            self.b.submit("birth", {"event_day": 100, "place": "phc-1"}, day=100)
        self.assertIn("sex", str(cm.exception))
        self.assertIn("mother_id", str(cm.exception))
        with self.assertRaises(CRVSError):
            self.b.submit("death", {"event_day": 100, "place": "home",
                                    "sex": "M"}, day=100)  # age_days missing

    def test_future_event_refused(self):
        with self.assertRaises(CRVSError):
            self.b.submit("birth", _birth("b1", day=200), day=100)

    def test_offline_queue_then_sync(self):
        self.b.note_event("birth", "b1")
        self.b.submit("birth", _birth("b1"), day=100)
        self.assertEqual(len(self.b.queue), 1)
        out = self.b.sync(lambda r: f"REG-{r['event_id']}", day=102)
        self.assertEqual(out["synced"], 1)
        self.assertEqual(self.b.queue, [])
        self.assertEqual(self.b.registered[0]["reg_no"], "REG-b1")

    def test_failed_sync_keeps_record_queued(self):
        self.b.submit("birth", _birth("b1"), day=100)

        def dead_link(r):
            raise ConnectionError("registry unreachable")
        out = self.b.sync(dead_link, day=101)
        self.assertEqual(out["synced"], 0)
        self.assertEqual(out["still_queued"], 1)
        # next attempt with a live link succeeds — nothing was dropped
        out = self.b.sync(lambda r: "REG-1", day=103)
        self.assertEqual(out["synced"], 1)

    def test_ack_without_reg_no_stays_queued(self):
        self.b.submit("birth", _birth("b1"), day=100)
        out = self.b.sync(lambda r: None, day=101)
        self.assertEqual(out["still_queued"], 1)

    def test_invisibility_report(self):
        for eid in ("b1", "b2", "b3"):
            self.b.note_event("birth", eid)
        self.b.submit("birth", _birth("b1"), day=100)
        self.b.sync(lambda r: "R1", day=100)
        self.b.submit("birth", _birth("b2"), day=100)  # still queued
        rep = self.b.invisibility_report(as_of=100)
        bk = rep["by_kind"]["birth"]
        self.assertEqual(bk["known_events"], 3)
        self.assertEqual(bk["registered"], 1)
        self.assertEqual(bk["queued"], 1)
        self.assertEqual(bk["unregistered_unqueued"], 1)  # b3 — the finding

    def test_stale_queue_surfaced(self):
        self.b.submit("birth", _birth("b1"), day=100)
        rep = self.b.invisibility_report(as_of=100 + QUEUE_STALE_DAYS + 1)
        self.assertEqual(rep["queue_stale"], 1)

    def test_unknown_event_kind_refused(self):
        with self.assertRaises(CRVSError):
            self.b.note_event("marriage", "x1")


if __name__ == "__main__":
    unittest.main()
