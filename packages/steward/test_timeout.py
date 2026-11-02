import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from steward import TimeoutClock, TIMEOUT_DAYS


def make_clock():
    c = TimeoutClock()
    c.start_course("ABX-1", patient_id="p1", drug_code="NG-CIPRO500",
                   route="iv", day=0, indication="pyelonephritis",
                   prescriber="dr-1")
    return c


class TestTimeoutClock(unittest.TestCase):
    def test_fail_closed_start(self):
        c = TimeoutClock()
        with self.assertRaises(ValueError):  # no indication
            c.start_course("A1", patient_id="p", drug_code="NG-AMOX500",
                           route="oral", day=0, indication="",
                           prescriber="dr-1")
        with self.assertRaises(ValueError):  # unsigned
            c.start_course("A1", patient_id="p", drug_code="NG-AMOX500",
                           route="oral", day=0, indication="uti",
                           prescriber="")
        with self.assertRaises(ValueError):  # bad route
            c.start_course("A1", patient_id="p", drug_code="NG-AMOX500",
                           route="pr", day=0, indication="uti",
                           prescriber="dr-1")
        with self.assertRaises(ValueError):  # sub-day timeout is noise
            TimeoutClock(timeout_days=0)
        c.start_course("A1", patient_id="p", drug_code="NG-AMOX500",
                       route="oral", day=0, indication="uti",
                       prescriber="dr-1")
        with self.assertRaises(ValueError):  # duplicate
            c.start_course("A1", patient_id="p", drug_code="NG-AMOX500",
                           route="oral", day=0, indication="uti",
                           prescriber="dr-1")

    def test_timeout_comes_due_overdue_first(self):
        c = make_clock()
        c.start_course("ABX-2", patient_id="p2", drug_code="NG-AMOX500",
                       route="oral", day=1, indication="otitis",
                       prescriber="dr-2")
        self.assertEqual(c.timeouts_due(as_of=1), [])
        due = c.timeouts_due(as_of=4)
        self.assertEqual([d["course_id"] for d in due],
                         ["ABX-1", "ABX-2"])  # 2d vs 1d overdue
        self.assertEqual(due[0]["overdue_days"], 2)
        self.assertEqual(due[0]["aware"], "Watch")

    def test_review_rearms_and_stop_ends_the_clock(self):
        c = make_clock()
        rev = c.review_timeout("ABX-1", day=2, by="pharm-1",
                               outcome="continue", note="await cultures")
        self.assertEqual(rev["outcome"], "continue")
        self.assertEqual(c.timeouts_due(as_of=3), [])   # re-armed
        self.assertEqual(len(c.timeouts_due(as_of=4)), 1)  # due again
        c.review_timeout("ABX-1", day=4, by="pharm-1", outcome="stop",
                         note="cultures negative")
        self.assertEqual(c.timeouts_due(as_of=99), [])
        with self.assertRaises(ValueError):  # a stopped course reviews nothing
            c.review_timeout("ABX-1", day=5, by="pharm-1",
                             outcome="continue")

    def test_review_fail_closed(self):
        c = make_clock()
        with self.assertRaises(ValueError):
            c.review_timeout("ghost", day=2, by="pharm-1",
                             outcome="stop")
        with self.assertRaises(ValueError):
            c.review_timeout("ABX-1", day=2, by="pharm-1",
                             outcome="shrug")
        with self.assertRaises(ValueError):
            c.review_timeout("ABX-1", day=2, by="", outcome="stop")

    def test_iv_to_oral_switch_flips_route(self):
        c = make_clock()
        c.review_timeout("ABX-1", day=2, by="pharm-1",
                         outcome="iv-to-oral", note="afebrile 48h")
        self.assertEqual(c.courses["ABX-1"]["route"], "oral")

    def test_report_denominators(self):
        c = make_clock()
        rep = c.timeout_report(as_of=5)
        self.assertEqual(rep["courses"], 1)
        self.assertEqual(rep["active"], 1)
        self.assertEqual(rep["overdue_now"], 1)
        c.review_timeout("ABX-1", day=2, by="p", outcome="stop")
        rep = c.timeout_report(as_of=5)
        self.assertEqual(rep["stopped"], 1)
        self.assertEqual(rep["overdue_now"], 0)


if __name__ == "__main__":
    unittest.main()
