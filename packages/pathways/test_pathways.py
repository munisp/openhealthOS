
import unittest
from pathways import (PathwayRegistry, PathwayCase, PathwayError,
                      variance_profile)


def _reg():
    r = PathwayRegistry()
    r.propose("malaria-adult", steps=["confirm", "treat", "review-d3"],
              by="dr-a")
    r.ratify("malaria-adult", by="dr-b")
    return r


class TestPathways(unittest.TestCase):
    def test_unratified_pathway_refused(self):
        r = PathwayRegistry()
        r.propose("x", steps=["a"], by="dr-a")
        with self.assertRaises(PathwayError):
            PathwayCase(r, "x", patient_ref="p1", day=1)

    def test_four_eyes(self):
        r = PathwayRegistry()
        r.propose("x", steps=["a"], by="dr-a")
        with self.assertRaises(PathwayError):
            r.ratify("x", by="dr-a")

    def test_variance_needs_declared_reason(self):
        c = PathwayCase(_reg(), "malaria-adult", patient_ref="p1", day=1)
        with self.assertRaises(PathwayError):
            c.vary("treat", reason="felt-like-it")

    def test_not_done_is_stated(self):
        c = PathwayCase(_reg(), "malaria-adult", patient_ref="p1", day=1)
        c.complete("confirm")
        c.not_done("treat", reason="stock-out")
        rep = c.report()
        self.assertEqual(rep["not_done"], 1)
        self.assertIn("stock-out", rep["variance_reasons"])

    def test_double_completion_refused(self):
        c = PathwayCase(_reg(), "malaria-adult", patient_ref="p1", day=1)
        c.complete("confirm")
        with self.assertRaises(PathwayError):
            c.complete("confirm")

    def test_variance_profile_ranks_reasons(self):
        reg = _reg()
        cases = []
        for i in range(3):
            c = PathwayCase(reg, "malaria-adult", patient_ref=f"p{i}", day=1)
            c.vary("treat", reason="stock-out")
            cases.append(c)
        c4 = PathwayCase(reg, "malaria-adult", patient_ref="p3", day=1)
        c4.vary("review-d3", reason="patient-choice")
        cases.append(c4)
        prof = variance_profile(cases)
        self.assertEqual(list(prof)[0], "stock-out")
        self.assertEqual(prof["stock-out"], 3)

    def test_unknown_step_refused(self):
        c = PathwayCase(_reg(), "malaria-adult", patient_ref="p1", day=1)
        with self.assertRaises(PathwayError):
            c.complete("amputate")


if __name__ == "__main__":
    unittest.main()
