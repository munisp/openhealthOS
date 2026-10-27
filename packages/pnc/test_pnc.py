import unittest
from pnc import PNCRegister, PNCError, SCHEDULE, NEWBORN_DANGER_SIGNS


class TestPNC(unittest.TestCase):
    def setUp(self):
        self.r = PNCRegister()
        self.r.register_birth("nb1", birth_day=10, place="home", attendant="tba")

    def test_schedule_shape(self):
        self.assertEqual([(c["earliest"], c["latest"]) for c in SCHEDULE],
                         [(0, 2), (7, 14), (35, 49)])

    def test_due_and_overdue(self):
        due = self.r.due("nb1", 12)  # age 2 — contact 1 last day
        self.assertEqual(len(due), 1)
        self.assertFalse(due[0]["overdue"])
        due = self.r.due("nb1", 15)  # age 5 — contact 1 overdue
        self.assertTrue(due[0]["overdue"])
        self.assertEqual(due[0]["days_overdue"], 3)

    def test_on_time_contact(self):
        rec = self.r.record_contact("nb1", 1, day=11, cadre="chw")
        self.assertTrue(rec["on_time"])
        self.assertFalse(rec["escalate"])
        self.assertEqual(self.r.due("nb1", 12), [])

    def test_late_contact_still_recorded_but_flagged(self):
        rec = self.r.record_contact("nb1", 1, day=14, cadre="chw")
        self.assertFalse(rec["on_time"])

    def test_danger_signs_escalate(self):
        rec = self.r.record_contact("nb1", 1, day=11, cadre="chw",
                                    danger_signs=["not-feeding", "convulsions"])
        self.assertTrue(rec["escalate"])

    def test_unknown_danger_sign_refused(self):
        with self.assertRaises(PNCError):
            self.r.record_contact("nb1", 1, day=11, cadre="chw",
                                  danger_signs=["witchcraft"])

    def test_duplicate_contact_and_birth_refused(self):
        self.r.record_contact("nb1", 1, day=11, cadre="chw")
        with self.assertRaises(PNCError):
            self.r.record_contact("nb1", 1, day=12, cadre="chw")
        with self.assertRaises(PNCError):
            self.r.register_birth("nb1", birth_day=10, place="phc", attendant="mw")

    def test_coverage_denominators(self):
        self.r.record_contact("nb1", 1, day=11, cadre="chw")
        self.r.register_birth("nb2", birth_day=10, place="home", attendant="none")
        cov = self.r.coverage(as_of=16)  # both past contact-1 window
        c1 = cov["contacts"]["1"]
        self.assertEqual(c1["on_time"], 1)
        self.assertEqual(c1["missed"], 1)
        self.assertEqual(cov["births_registered"], 2)
        # contact 2 pending (age 6 < 7)
        self.assertEqual(cov["contacts"]["2"]["pending"], 2)

    def test_contact_before_birth_refused(self):
        with self.assertRaises(PNCError):
            self.r.record_contact("nb1", 1, day=9, cadre="chw")

    def test_all_danger_signs_known(self):
        for sign in NEWBORN_DANGER_SIGNS:
            r = PNCRegister()
            r.register_birth("x", birth_day=0, place="phc", attendant="mw")
            rec = r.record_contact("x", 1, day=1, cadre="nurse",
                                   danger_signs=[sign])
            self.assertTrue(rec["escalate"])


if __name__ == "__main__":
    unittest.main()
