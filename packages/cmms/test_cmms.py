#!/usr/bin/env python3
"""CMMS tests: PM scheduling, overdue worklists, critical-down auto
work orders, downtime accounting, availability."""
import unittest

from cmms import CMMS, Asset, CMMSError

DAY = "2026-05-01"


def build():
    c = CMMS()
    c.register(Asset("OX-1", "Oxygen plant 1", "oxygen-plant", "FAC-1",
                     criticality="critical", pm_interval_days=30), actor="admin")
    c.register(Asset("GEN-1", "Generator", "generator", "FAC-1",
                     criticality="essential", pm_interval_days=60), actor="admin")
    return c


class TestMaintenance(unittest.TestCase):
    def test_pm_schedule_and_overdue(self):
        c = build()
        wo = c.schedule_pm("OX-1", DAY, actor="admin")
        self.assertEqual(wo.due, "2026-05-31")
        self.assertEqual(c.overdue("2026-06-05"), [wo])
        c.complete(wo.wo_id, "2026-06-02", actor="tech-1", note="filters replaced")
        self.assertEqual(c.overdue("2026-06-05"), [])
        self.assertEqual(c.assets["OX-1"].last_pm, "2026-06-02")
        # next PM chains off the completed one
        wo2 = c.schedule_pm("OX-1", "2026-06-02", actor="admin")
        self.assertEqual(wo2.due, "2026-07-02")

    def test_complete_twice_refused(self):
        c = build()
        wo = c.schedule_pm("GEN-1", DAY, actor="admin")
        c.complete(wo.wo_id, DAY, actor="t")
        with self.assertRaises(CMMSError):
            c.complete(wo.wo_id, DAY, actor="t")


class TestDowntime(unittest.TestCase):
    def test_critical_down_opens_urgent_repair(self):
        c = build()
        c.report_down("OX-1", DAY, reason="compressor failure", actor="nurse-1")
        repairs = [w for w in c.work_orders.values() if w.kind == "repair"]
        self.assertEqual(len(repairs), 1)
        self.assertIn("URGENT", repairs[0].note)

    def test_downtime_counted_in_availability(self):
        c = build()
        c.report_down("GEN-1", "2026-05-01", reason="fuel line", actor="x")
        c.report_up("GEN-1", "2026-05-11", actor="tech")
        avail = {a["asset_id"]: a for a in c.availability("FAC-1", "2026-07-30", 90)}
        self.assertAlmostEqual(avail["GEN-1"]["availability"], round(1 - 10 / 90, 4))

    def test_currently_down_counts_to_today(self):
        c = build()
        c.report_down("GEN-1", "2026-05-01", reason="x", actor="x")
        avail = {a["asset_id"]: a for a in c.availability("FAC-1", "2026-05-21", 90)}
        self.assertEqual(avail["GEN-1"]["status"], "down")
        self.assertAlmostEqual(avail["GEN-1"]["availability"], round(1 - 20 / 90, 4))

    def test_reason_and_actor_required(self):
        c = build()
        with self.assertRaises(CMMSError):
            c.report_down("GEN-1", DAY, reason="", actor="x")


if __name__ == "__main__":
    unittest.main()
