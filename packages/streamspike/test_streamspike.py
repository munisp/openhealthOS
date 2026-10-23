"""Pinned invariants for the ADR-0066 stream contract (reference engine)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from refstream import (RefTopic, RefConsumer, per_key_order_ok, load_fixture,  # noqa: E402
                   build_stream)


class TestStreamContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = load_fixture()
        cls.topic = build_stream(cls.events)

    def test_fixture_is_multifacility(self):
        self.assertEqual(len(self.events), 24)
        self.assertEqual(len({e["facility_id"] for e in self.events}), 3)

    def test_produce_assigns_monotonic_offsets(self):
        self.assertEqual([r["offset"] for r in self.topic.log], list(range(24)))

    def test_per_key_ordering_holds(self):
        self.assertTrue(per_key_order_ok(self.topic))

    def test_consumer_delivers_each_event_exactly_once(self):
        c = RefConsumer(self.topic)
        got = c.poll()
        self.assertEqual(len(got), 24)
        self.assertEqual(len({e["event_id"] for e in got}), 24)

    def test_empty_poll_after_catchup(self):
        c = RefConsumer(self.topic)
        c.poll()
        self.assertEqual(c.poll(), [])

    def test_at_least_once_replay_dedupes_by_event_id(self):
        c = RefConsumer(self.topic)
        c.poll()
        # consumer crashes, restarts from an earlier offset: the log
        # re-delivers, the consumer must not re-apply
        again = c.replay_from(0)
        self.assertEqual(again, [])  # all already seen
        self.assertEqual(len(c.delivered), 24)

    def test_replay_picks_up_new_events_only(self):
        t = build_stream(self.events[:10])
        c = RefConsumer(t)
        c.poll()
        for ev in self.events[10:]:
            t.produce(ev["facility_id"], ev)
        got = c.replay_from(0)  # restart from scratch
        self.assertEqual([e["event_id"] for e in got],
                         [e["event_id"] for e in self.events[10:]])
        self.assertEqual(len(c.delivered), 24)

    def test_per_key_seq_gaps_detect_out_of_order(self):
        t = RefTopic("x")
        t.produce("F1", {"event_id": "a", "seq": 2})
        t.produce("F1", {"event_id": "b", "seq": 1})  # regression
        self.assertFalse(per_key_order_ok(t))


if __name__ == "__main__":
    unittest.main()
