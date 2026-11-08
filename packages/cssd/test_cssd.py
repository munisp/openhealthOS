"""Tests for packages/cssd — sterile supply chain."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cssd import (  # noqa: E402
    AutoclaveCycle, SterilePack, CSSDRegistry, Error,
)


def good_cycle(cid="C1", day=1):
    return AutoclaveCycle(cid, "pre-vac-134", temp_c=134.5,
                          duration_minutes=4, day=day, bi_result="negative")


class TestCycle(unittest.TestCase):
    def test_valid_cycle_passes(self):
        c = good_cycle()
        self.assertFalse(c.failed)

    def test_low_temp_fails(self):
        c = AutoclaveCycle("C2", "pre-vac-134", temp_c=120.0,
                           duration_minutes=4, day=1)
        self.assertTrue(c.failed)
        self.assertIn("temp", c.failure_reasons[0])

    def test_short_duration_fails(self):
        c = AutoclaveCycle("C3", "gravity-121", temp_c=122.0,
                           duration_minutes=10, day=1)
        self.assertTrue(c.failed)

    def test_positive_bi_fails(self):
        c = good_cycle("C4")
        c_pos = AutoclaveCycle("C5", "pre-vac-134", temp_c=135.0,
                               duration_minutes=4, day=1,
                               bi_result="positive")
        self.assertTrue(c_pos.failed)
        self.assertIn("POSITIVE", c_pos.failure_reasons[0])


class TestPack(unittest.TestCase):
    def setUp(self):
        self.reg = CSSDRegistry("FAC-1")
        self.reg.register_cycle(good_cycle("C1", day=1))

    def test_pack_lifecycle(self):
        p = SterilePack("P1", ["scalpel", "forceps", "retractor"],
                        assembler="tech-1", day=1)
        ok, _ = p.usable_on(1)
        self.assertFalse(ok)  # no cycle yet
        self.reg.register_pack(p, "C1")
        ok, reason = p.usable_on(1)
        self.assertTrue(ok, reason)

    def test_failed_cycle_quarantines(self):
        self.reg.register_cycle(AutoclaveCycle(
            "CBAD", "pre-vac-134", temp_c=100.0, duration_minutes=4, day=1))
        p = SterilePack("P2", ["clamp"], assembler="tech-1", day=1)
        self.reg.register_pack(p, "CBAD")
        ok, reason = p.usable_on(1)
        self.assertFalse(ok)
        self.assertIn("quarantined", reason)

    def test_expiry_computed(self):
        p = SterilePack("P3", ["drape"], assembler="tech-1", day=1,
                        packaging="wrapped")
        self.reg.register_pack(p, "C1")
        self.assertEqual(p.expiry_day, 1 + 30)
        ok, _ = p.usable_on(31)
        self.assertTrue(ok)
        ok, reason = p.usable_on(32)
        self.assertFalse(ok)
        self.assertIn("expired", reason)

    def test_issue_to_theatre(self):
        p = SterilePack("P4", ["kit"], assembler="tech-1", day=1)
        self.reg.register_pack(p, "C1")
        out = self.reg.issue_to_theatre("P4", day=5, issued_to="nurse-t")
        self.assertEqual(out["pack_id"], "P4")

    def test_unnamed_recipient_refused(self):
        p = SterilePack("P5", ["kit"], assembler="tech-1", day=1)
        self.reg.register_pack(p, "C1")
        with self.assertRaises(Error):
            self.reg.issue_to_theatre("P5", day=5, issued_to="")

    def test_daily_report(self):
        self.reg.register_pack(
            SterilePack("P6", ["a"], assembler="t", day=1), "C1")
        self.reg.register_cycle(AutoclaveCycle(
            "CB2", "pre-vac-134", temp_c=100.0, duration_minutes=4, day=1))
        self.reg.register_pack(
            SterilePack("P7", ["b"], assembler="tech-1", day=1), "CB2")
        rep = self.reg.daily_report(1)
        self.assertEqual(rep["usable"], 1)
        self.assertEqual(rep["quarantined"], 1)
        self.assertEqual(len(rep["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
