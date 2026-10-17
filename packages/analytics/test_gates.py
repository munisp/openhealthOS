import unittest

from gates import (claims_turnaround, offline_continuity, staff_weekly_active,
                   stockout_visibility, sync_reliability)

EVENTS = [
    # 10 encounters, 9 captured offline → 90% continuity
    *[{"type": "encounter", "captured_offline": True,
       "ts": "2026-10-10T08:00:00Z", "reconciled_at": "2026-10-10T08:10:00Z"}
      for _ in range(9)],
    {"type": "encounter", "captured_offline": False,
     "ts": "2026-10-10T08:00:00Z", "reconciled_at": "2026-10-10T08:05:00Z"},
    # claims: 3 settled in 2, 5, 13 days → median 5
    {"type": "claim_submitted", "claim_id": "C-1", "ts": "2026-10-01T00:00:00Z"},
    {"type": "claim_settled", "claim_id": "C-1", "ts": "2026-10-03T00:00:00Z"},
    {"type": "claim_submitted", "claim_id": "C-2", "ts": "2026-10-01T00:00:00Z"},
    {"type": "claim_settled", "claim_id": "C-2", "ts": "2026-10-06T00:00:00Z"},
    {"type": "claim_submitted", "claim_id": "C-3", "ts": "2026-10-01T00:00:00Z"},
    {"type": "claim_settled", "claim_id": "C-3", "ts": "2026-10-14T00:00:00Z"},
    # stock reports: 8 facility-days
    *[{"type": "stock_report", "facility": f"PHC-{i:03d}", "ts": "2026-10-10T09:00:00Z"}
      for i in range(8)],
    # staff activity: 7 of 10
    *[{"type": "staff_activity", "staff_id": f"S-{i}"} for i in range(7)],
]


class TestGates(unittest.TestCase):
    def test_offline_continuity(self):
        r = offline_continuity(EVENTS)
        self.assertEqual(r["offline_pct"], 90.0)
        self.assertTrue(r["pass"])

    def test_sync_reliability(self):
        r = sync_reliability(EVENTS)
        self.assertEqual(r["within_sla_pct"], 100.0)  # all ≤15 min
        self.assertTrue(r["pass"])

    def test_claims_turnaround(self):
        r = claims_turnaround(EVENTS)
        self.assertEqual(r["median_days"], 5)
        self.assertTrue(r["pass"])

    def test_claims_turnaround_fails_over_14(self):
        ev = [{"type": "claim_submitted", "claim_id": "C-9", "ts": "2026-10-01T00:00:00Z"},
              {"type": "claim_settled", "claim_id": "C-9", "ts": "2026-10-20T00:00:00Z"}]
        r = claims_turnaround(ev)
        self.assertEqual(r["median_days"], 19)
        self.assertFalse(r["pass"])

    def test_stockout_visibility(self):
        r = stockout_visibility(EVENTS, facilities=5, days=2)
        self.assertEqual(r["pct"], 80.0)
        self.assertTrue(r["pass"])

    def test_staff_wau(self):
        r = staff_weekly_active(EVENTS, trained_staff=10, weeks=1)
        self.assertEqual(r["active_pct"], 70.0)
        self.assertTrue(r["pass"])

    def test_empty_inputs_safe(self):
        self.assertIsNone(offline_continuity([])["offline_pct"])
        self.assertIsNone(claims_turnaround([])["median_days"])
        self.assertIsNone(sync_reliability([])["within_sla_pct"])


if __name__ == "__main__":
    unittest.main()
