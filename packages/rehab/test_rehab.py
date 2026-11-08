"""Tests for packages/rehab — physiotherapy and rehabilitation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rehab import RehabCourse, Error  # noqa: E402


def make_course():
    return RehabCourse("RC-1", "Pax-1", "post-stroke hemiparesis",
                       therapist="physio-ade", start_day=1)


class TestGoals(unittest.TestCase):
    def test_goal_with_measure_baseline_target(self):
        c = make_course()
        g = c.add_goal("G1", "rom-degrees", 40, 90, 28)
        self.assertEqual(g.state, "active")

    def test_unknown_measure_refused(self):
        c = make_course()
        with self.assertRaises(Error):
            c.add_goal("G1", "how-they-feel", 1, 10, 28)

    def test_implausible_bounds_refused(self):
        c = make_course()
        with self.assertRaises(Error):
            c.add_goal("G1", "rom-degrees", 500, 600, 28)
        with self.assertRaises(Error):
            c.add_goal("G1", "mrc-grade", 0, 9, 28)

    def test_duplicate_goal_refused(self):
        c = make_course()
        c.add_goal("G1", "rom-degrees", 40, 90, 28)
        with self.assertRaises(Error):
            c.add_goal("G1", "barthel", 40, 90, 28)


class TestSessions(unittest.TestCase):
    def test_session_dosage_recorded(self):
        c = make_course()
        c.record_session(day=2, modality="gait training",
                         duration_minutes=45,
                         patient_response="tolerated well")
        self.assertEqual(c.sessions[0]["duration_minutes"], 45)

    def test_dna_recorded_not_erased(self):
        c = make_course()
        c.record_session(day=3, modality=None, duration_minutes=0,
                         patient_response="", state="dna")
        self.assertEqual(c.sessions[0]["state"], "dna")
        self.assertEqual(c.sessions[0]["duration_minutes"], 0)

    def test_completed_session_needs_modality(self):
        c = make_course()
        with self.assertRaises(Error):
            c.record_session(day=2, modality="", duration_minutes=30,
                             patient_response="ok")


class TestDischarge(unittest.TestCase):
    def test_open_goals_block_discharge(self):
        c = make_course()
        c.add_goal("G1", "rom-degrees", 40, 90, 28)
        with self.assertRaises(Error):
            c.discharge_summary(by="physio-ade", day=30)

    def test_full_course_lifecycle(self):
        c = make_course()
        g = c.add_goal("G1", "rom-degrees", 40, 90, 28)
        c.record_session(day=2, modality="ROM exercises",
                         duration_minutes=30, patient_response="ok")
        c.record_session(day=5, modality="ROM exercises",
                         duration_minutes=30, patient_response="ok")
        c.record_session(day=8, modality=None, duration_minutes=0,
                         patient_response="", state="dna")
        g.close("met", outcome_value=92, by="physio-ade", day=28)
        d = c.discharge_summary(by="physio-ade", day=28)
        self.assertEqual(d["goals"]["G1"]["state"], "met")
        self.assertEqual(d["sessions_completed"], 2)
        self.assertEqual(d["sessions_dna"], 1)
        out = c.as_dict()
        self.assertEqual(len(out["sha256"]), 64)
        with self.assertRaises(Error):
            c.record_session(day=29, modality="x", duration_minutes=10,
                             patient_response="ok")


if __name__ == "__main__":
    unittest.main()
