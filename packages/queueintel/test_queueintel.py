"""Dedicated suite for queueintel.py — starvation guard under test."""
import unittest
from queueintel import Queue, STARVATION_CAP_MS


class TestQueueIntel(unittest.TestCase):
    def test_emergency_before_arrival_order(self):
        q = Queue()
        q.arrive("p-routine", "routine", now=0)
        q.arrive("p-emerg", "emergency", now=100)
        self.assertEqual(q.next(now=200)["patient_ref"], "p-emerg")

    def test_unknown_class_refused(self):
        q = Queue()
        with self.assertRaises(ValueError):
            q.arrive("p1", "vip", now=0)

    def test_starvation_promotes_and_documents(self):
        # promotion lifts routine INTO visit class; arrival order then
        # breaks the tie — a promoted routine that arrived first wins.
        q = Queue()
        q.arrive("p-routine", "routine", now=0)
        q.arrive("p-visit", "visit", now=10)
        later = STARVATION_CAP_MS + 1
        nxt = q.next(now=later)
        self.assertEqual(nxt["patient_ref"], "p-routine")
        self.assertTrue(nxt["promoted"])  # documented, not silent
        # before the cap, the visit patient goes first
        q2 = Queue()
        q2.arrive("p-routine", "routine", now=0)
        q2.arrive("p-visit", "visit", now=10)
        self.assertEqual(q2.next(now=100)["patient_ref"], "p-visit")

    def test_empty_queue_is_honest_none(self):
        self.assertIsNone(Queue().next(now=0))

    def test_serve_removes(self):
        q = Queue()
        q.arrive("p1", "visit", now=0)
        q.serve(now=1)
        self.assertIsNone(q.next(now=2))


if __name__ == "__main__":
    unittest.main()
