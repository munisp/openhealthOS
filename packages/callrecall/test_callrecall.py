"""test_callrecall.py — the register gates eligibility, the ladder is
sequential, exits don't erase, and coverage shows its denominator."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from callrecall import (CallRecallError, CallRecallRegister,  # noqa: E402
                        CALL_RECALL_VERSION)


def reg():
    return CallRecallRegister("cervix")


class TestEligibility(unittest.TestCase):
    def test_age_band_is_a_gate(self):
        r = reg()
        r.enroll("w-35", birth_day=0, day=35 * 365)         # 35y — in
        with self.assertRaises(CallRecallError):            # 24y — out
            r.enroll("w-24", birth_day=0, day=24 * 365)
        with self.assertRaises(CallRecallError):            # 55y — out
            r.enroll("w-55", birth_day=0, day=55 * 365)

    def test_hiv_band_widens_and_shortens(self):
        r = reg()
        r.enroll("w-hiv", birth_day=0, day=26 * 365, hiv_positive=True)
        self.assertEqual(r.interval_days("w-hiv"), 30 * 36)
        r.enroll("w-gen", birth_day=0, day=35 * 365)
        self.assertEqual(r.interval_days("w-gen"), 30 * 60)

    def test_duplicate_enroll_refuses(self):
        r = reg()
        r.enroll("w-1", birth_day=0, day=30 * 365)
        with self.assertRaises(CallRecallError):
            r.enroll("w-1", birth_day=0, day=30 * 365)


class TestLadder(unittest.TestCase):
    def test_ladder_is_sequential(self):
        r = reg()
        r.enroll("w-1", birth_day=0, day=30 * 365)
        with self.assertRaises(CallRecallError):
            r.record_step("w-1", step="reminder-1", day=5,
                          channel="sms")   # invite first
        r.record_step("w-1", step="invite", day=0, channel="sms")
        with self.assertRaises(CallRecallError):
            r.record_step("w-1", step="invite", day=6, channel="sms")

    def test_channel_required(self):
        r = reg()
        r.enroll("w-1", birth_day=0, day=30 * 365)
        with self.assertRaises(CallRecallError):
            r.record_step("w-1", step="invite", day=0,
                          channel="shout-really-loud")

    def test_steps_due_overdue_first(self):
        r = reg()
        day0 = 30 * 365
        r.enroll("w-old", birth_day=0, day=day0)            # due at day0
        r.enroll("w-new", birth_day=0, day=day0 + 30)       # due day0+30
        due = r.steps_due(as_of=day0 + 40)
        self.assertEqual([d["patient_id"] for d in due],
                         ["w-old", "w-new"])
        self.assertTrue(all(d["step"] == "invite" for d in due))
        r.record_step("w-old", step="invite", day=day0 + 40,
                      channel="sms")
        # a late invite does NOT pause the ladder: reminder-1 was due
        # 14 days after round start, so it is already overdue — and
        # overdue-first puts it ahead of w-new's invite (26 vs 10 days)
        due2 = r.steps_due(as_of=day0 + 40)
        self.assertEqual([(d["patient_id"], d["step"]) for d in due2],
                         [("w-old", "reminder-1"), ("w-new", "invite")])
        # reminder-1 comes due 14 days after round start
        due3 = r.steps_due(as_of=day0 + 55)
        self.assertEqual(due3[0]["step"], "reminder-1")

    def test_lapsed_after_ladder_exhausted(self):
        r = reg()
        day0 = 30 * 365
        r.enroll("w-1", birth_day=0, day=day0)
        r.record_step("w-1", step="invite", day=day0, channel="sms")
        r.record_step("w-1", step="reminder-1", day=day0 + 14,
                      channel="sms")
        r.record_step("w-1", step="reminder-2", day=day0 + 42,
                      channel="chw-visit")
        self.assertEqual(r.steps_due(as_of=day0 + 100), [])
        lapsed = r.lapsed(as_of=day0 + 100)
        self.assertEqual([l["patient_id"] for l in lapsed], ["w-1"])

    def test_exited_never_invited(self):
        r = reg()
        day0 = 30 * 365
        r.enroll("w-1", birth_day=0, day=day0)
        r.exit("w-1", day=day0 + 10, reason="no-cervix")
        self.assertEqual(r.steps_due(as_of=day0 + 30), [])
        with self.assertRaises(CallRecallError):
            r.record_step("w-1", step="invite", day=day0 + 30,
                          channel="sms")
        with self.assertRaises(CallRecallError):
            r.exit("w-1", day=day0 + 31, reason="moved-away")


class TestScreenedAndCoverage(unittest.TestCase):
    def test_mark_screened_reschedules_and_reopens_ladder(self):
        r = reg()
        r.enroll("w-1", birth_day=0, day=30 * 365, hiv_positive=True)
        r.mark_screened("w-1", day=20)
        self.assertEqual(r.due_day("w-1"), 20 + 30 * 36)
        self.assertEqual(r.steps_due(as_of=20 + 30 * 36 - 1), [])
        due = r.steps_due(as_of=20 + 30 * 36)
        self.assertEqual(due[0]["step"], "invite")

    def test_coverage_shows_its_denominator(self):
        r = reg()
        r.enroll("w-a", birth_day=0, day=30 * 365)
        r.enroll("w-b", birth_day=0, day=30 * 365)
        r.enroll("w-c", birth_day=0, day=30 * 365)
        r.mark_screened("w-a", day=10)
        cov = r.coverage(as_of=20)
        self.assertEqual(cov["eligible"], 3)
        self.assertEqual(cov["screened_in_interval"], 1)
        self.assertEqual(cov["coverage_rate"], round(1 / 3, 3))
        self.assertEqual(cov["never_screened"], 2)
        self.assertEqual(cov["register_version"], CALL_RECALL_VERSION)

    def test_screened_out_of_interval_is_not_covered(self):
        r = reg()
        r.enroll("w-a", birth_day=0, day=30 * 365)
        r.mark_screened("w-a", day=0)
        cov = r.coverage(as_of=30 * 60 + 1)   # one day past 60 months
        self.assertEqual(cov["screened_in_interval"], 0)

    def test_hiv_status_change_is_recorded_not_rewritten(self):
        r = reg()
        r.enroll("w-a", birth_day=0, day=30 * 365)
        r.update_hiv_status("w-a", hiv_positive=True, day=40)
        self.assertEqual(r.interval_days("w-a"), 30 * 36)
        self.assertEqual(r.enrolled["w-a"]["hiv_changes"],
                         [{"hiv_positive": True, "day": 40}])
        with self.assertRaises(CallRecallError):
            r.update_hiv_status("w-a", hiv_positive=True, day=41)


if __name__ == "__main__":
    unittest.main()
