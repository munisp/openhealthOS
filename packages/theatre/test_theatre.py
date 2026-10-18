import unittest

from theatre import Theatre, Error, CHECKLIST


def complete_phase(th, case_id, phase, actor="nurse-1"):
    for item in CHECKLIST[phase]:
        th.check(case_id, phase, item, actor)


class TestTheatre(unittest.TestCase):
    def test_booking_and_emergency_bump(self):
        th = Theatre("TH-1")
        th.book("2026-10-20", "P1", "hernia repair", "elective", "dr-a", "dr-b")
        th.book("2026-10-20", "P2", "appendectomy", "urgent", "dr-a", "dr-b")
        em = th.book("2026-10-20", "P3", "ruptured ectopic", "emergency", "dr-a", "dr-b")
        self.assertEqual(th.running_order("2026-10-20")[0], em.id)

    def test_cannot_start_without_full_sign_in(self):
        th = Theatre("TH-1")
        c = th.book("2026-10-20", "P1", "hernia", "elective", "dr-a", "dr-b")
        for item in CHECKLIST["sign_in"][:-1]:
            th.check(c.id, "sign_in", item, "nurse-1")
        with self.assertRaises(Error) as ctx:
            th.start(c.id, "nurse-1")
        self.assertIn("sign-in incomplete", str(ctx.exception))

    def test_phase_ordering_enforced(self):
        th = Theatre("TH-1")
        c = th.book("2026-10-20", "P1", "hernia", "elective", "dr-a", "dr-b")
        with self.assertRaises(Error):
            th.check(c.id, "time_out", "team_introductions", "nurse-1")
        complete_phase(th, c.id, "sign_in")
        th.check(c.id, "time_out", "team_introductions", "nurse-1")

    def test_full_flow(self):
        th = Theatre("TH-1")
        c = th.book("2026-10-20", "P1", "c-section", "emergency", "dr-a", "dr-b")
        complete_phase(th, c.id, "sign_in")
        th.start(c.id, "nurse-1")
        complete_phase(th, c.id, "time_out")
        complete_phase(th, c.id, "sign_out")
        th.complete(c.id, "nurse-1", "mother and baby well")
        self.assertEqual(c.state, "completed")

    def test_one_case_in_theatre_at_a_time(self):
        th = Theatre("TH-1")
        c1 = th.book("2026-10-20", "P1", "a", "elective", "dr-a", "dr-b")
        c2 = th.book("2026-10-20", "P2", "b", "elective", "dr-a", "dr-b")
        complete_phase(th, c1.id, "sign_in")
        complete_phase(th, c2.id, "sign_in")
        th.start(c1.id, "nurse-1")
        with self.assertRaises(Error):
            th.start(c2.id, "nurse-1")

    def test_cancel_needs_reason(self):
        th = Theatre("TH-1")
        c = th.book("2026-10-20", "P1", "hernia", "elective", "dr-a", "dr-b")
        with self.assertRaises(Error):
            th.cancel(c.id, "dr-a", "")
        th.cancel(c.id, "dr-a", "no oxygen — cylinder empty")
        self.assertEqual(c.state, "cancelled")

    def test_validation(self):
        th = Theatre("TH-1")
        with self.assertRaises(Error):
            th.book("2026-10-20", "P1", "x", "whenever", "dr-a", "dr-b")
        with self.assertRaises(Error):
            th.book("2026-10-20", "P1", "x", "elective", "", "dr-b")

    def test_utilisation(self):
        th = Theatre("TH-1")
        c1 = th.book("2026-10-20", "P1", "a", "elective", "dr-a", "dr-b")
        th.book("2026-10-20", "P2", "b", "elective", "dr-a", "dr-b")
        th.cancel(c1.id, "dr-a", "power cut")
        self.assertEqual(th.utilisation("2026-10-20")["utilisation_pct"], 50.0)


if __name__ == "__main__":
    unittest.main()
