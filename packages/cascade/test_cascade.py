import unittest
from cascade import CascadeRegister, CascadeError


class TestHIVCascade(unittest.TestCase):
    def setUp(self):
        self.r = CascadeRegister("hiv")
        self.r.enroll("p1", day=1, facility="phc-1")

    def test_happy_path(self):
        self.r.advance("p1", "tested", day=1, result="positive",
                       expected_return_day=8)
        self.r.advance("p1", "linked", day=8)
        self.r.advance("p1", "on-art", day=8, expected_return_day=38)
        self.assertEqual(self.r.patients["p1"]["stage_idx"], 2)

    def test_no_stage_jumping(self):
        with self.assertRaises(CascadeError):
            self.r.advance("p1", "on-art", day=1)
        self.r.advance("p1", "tested", day=1, result="negative")
        with self.assertRaises(CascadeError):
            self.r.advance("p1", "on-art", day=2)

    def test_testing_requires_result(self):
        with self.assertRaises(CascadeError):
            self.r.advance("p1", "tested", day=1)

    def test_tracer_list_overdue_first(self):
        self.r.advance("p1", "tested", day=1, result="positive",
                       expected_return_day=5)
        self.r.enroll("p2", day=1, facility="phc-1")
        self.r.advance("p2", "tested", day=1, result="positive",
                       expected_return_day=3)
        tl = self.r.tracer_list(as_of=10)
        self.assertEqual(len(tl), 2)
        self.assertEqual(tl[0]["patient_id"], "p2")  # 4 days overdue
        self.assertEqual(tl[1]["days_overdue"], 10 - 5 - 3)

    def test_exit_and_no_further_advance(self):
        self.r.exit("p1", day=5, reason="lost-to-follow-up")
        with self.assertRaises(CascadeError):
            self.r.advance("p1", "tested", day=6, result="positive")
        with self.assertRaises(CascadeError):
            self.r.exit("p1", day=6, reason="teleported")

    def test_funnel_denominators(self):
        self.r.advance("p1", "tested", day=1, result="positive")
        self.r.advance("p1", "linked", day=2)
        self.r.enroll("p2", day=1, facility="phc-1")
        self.r.advance("p2", "tested", day=1, result="negative")
        f = self.r.funnel(as_of=10)
        self.assertEqual(f["stage_counts"]["tested"], 2)
        self.assertEqual(f["stage_counts"]["linked"], 1)
        self.assertEqual(f["enrolled"], 2)


class TestTBCascade(unittest.TestCase):
    def test_stages_and_completion(self):
        r = CascadeRegister("tb")
        r.enroll("t1", day=1, facility="phc-2")
        r.advance("t1", "screened", day=1)
        r.advance("t1", "diagnosed", day=2, result="positive")
        r.advance("t1", "started", day=3, expected_return_day=31)
        r.advance("t1", "completed", day=180)
        r.advance("t1", "cured", day=181)
        f = r.funnel(as_of=200)
        self.assertEqual(f["stage_counts"]["cured"], 1)

    def test_unknown_program_refused(self):
        with self.assertRaises(CascadeError):
            CascadeRegister("measles")


if __name__ == "__main__":
    unittest.main()
