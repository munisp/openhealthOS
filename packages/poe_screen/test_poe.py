#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from poe_screen import AlertList, BorderPost, PoEError  # noqa: E402

class TestPoE(unittest.TestCase):
    def setUp(self):
        self.alerts = AlertList()
        self.alerts.publish("al-v1", ["Uganda", "DR Congo"], "2026-10-20")
        self.post = BorderPost("NAIA-ABUJA", self.alerts)

    def test_exposed_symptomatic_isolates_and_seeds_trace(self):
        r = self.post.screen("T-1", "al-v1", ["Uganda", "Ethiopia"],
                             38.6, ["headache"], "officer-a", "2026-10-21")
        self.assertEqual(r["disposition"], "isolate-and-notify")
        self.assertEqual(r["trace_seed"]["exposure_day"], "2026-10-21")

    def test_symptomatic_without_exposure_observes(self):
        r = self.post.screen("T-2", "al-v1", ["Kenya"], 38.9, [],
                             "officer-a", "2026-10-21")
        self.assertEqual(r["disposition"], "observe")

    def test_clean_passes(self):
        r = self.post.screen("T-3", "al-v1", ["Ghana"], 36.7, [],
                             "officer-a", "2026-10-21")
        self.assertEqual(r["disposition"], "pass")

    def test_stale_list_refuses_naming_current(self):
        self.alerts.publish("al-v2", ["Uganda", "DR Congo", "South Sudan"],
                            "2026-10-24")
        with self.assertRaises(PoEError) as ctx:
            self.post.screen("T-4", "al-v1", ["Uganda"], 37.0, [],
                             "officer-a", "2026-10-25")
        self.assertIn("al-v2", str(ctx.exception))

    def test_screener_required_and_lists_never_mutate(self):
        with self.assertRaises(PoEError):
            self.post.screen("T-5", "al-v1", [], 36.5, [], " ",
                             "2026-10-21")
        with self.assertRaises(PoEError):
            self.alerts.publish("al-v1", ["Uganda"], "2026-10-22")
        with self.assertRaises(PoEError):
            AlertList().publish("x", [], "2026-10-22")

    def test_stats(self):
        self.post.screen("T-1", "al-v1", ["Uganda"], 38.6, [], "o", "2026-10-21")
        self.post.screen("T-2", "al-v1", ["Ghana"], 36.5, [], "o", "2026-10-21")
        s = self.post.stats()
        self.assertEqual(s["isolate-and-notify"], 1)
        self.assertEqual(s["pass"], 1)

if __name__ == "__main__":
    unittest.main()
