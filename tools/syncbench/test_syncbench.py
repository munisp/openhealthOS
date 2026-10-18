#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

from syncbench import chaos_tail, make_event, percentile, verify_spill


class TestStats(unittest.TestCase):
    def test_percentile(self):
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(percentile([1, 2, 3, 4], 99), 4)
        self.assertEqual(percentile([], 99), 0.0)


class TestEvents(unittest.TestCase):
    def test_event_shape_matches_sync_envelope(self):
        ev = make_event(7)
        self.assertIn(ev["type"], ("encounter", "prescription", "dispensing",
                                   "vitals", "lab_order"))
        self.assertTrue(ev["id"].startswith("bench-"))
        self.assertIn("patient_id", ev["payload"])
        json.dumps(ev)  # serializable


class TestSpillChaos(unittest.TestCase):
    def test_torn_tail_tolerated_good_prefix_kept(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ndjson") as f:
            path = f.name
            f.write(json.dumps(make_event(1)).encode() + b"\n")
            f.write(json.dumps(make_event(2)).encode() + b"\n")
        try:
            result = chaos_tail(path)
            self.assertEqual(result["good_lines"], 2)
            self.assertEqual(result["corrupt_lines"], 1)
            self.assertTrue(result["tolerated"])
        finally:
            os.unlink(path)

    def test_sealed_lines_count_as_good(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ndjson") as f:
            path = f.name
            f.write(b"SX1:abcdef\n")
            f.write(json.dumps(make_event(3)).encode() + b"\n")
        try:
            result = verify_spill(path)
            self.assertEqual(result["good_lines"], 2)
            self.assertEqual(result["corrupt_lines"], 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
