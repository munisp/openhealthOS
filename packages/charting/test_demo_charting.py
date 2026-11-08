"""Demo-seed tests — determinism, stamps, and the gates visibly firing."""

import json
import os
import tempfile
import unittest

import demo_charting as chart_demo
from charting import ChartNote, OrderSetRegistry


class TestChartingDemo(unittest.TestCase):
    def test_deterministic(self):
        a = chart_demo.charting_payload(chart_demo.seed_charting())
        b = chart_demo.charting_payload(chart_demo.seed_charting())
        self.assertEqual(a, b)
        self.assertEqual(len(a["sha256"]), 64)

    def test_gates_fire_in_the_demo(self):
        p = chart_demo.charting_payload(chart_demo.seed_charting())
        gated = p["applications"][1]
        reasons = [r["reason"] for r in gated["refused"]]
        self.assertTrue(any("allergy conflict" in r for r in reasons))
        self.assertTrue(any("age" in r for r in reasons))

    def test_signed_notes_with_addendum_present(self):
        p = chart_demo.charting_payload(chart_demo.seed_charting())
        signed = [n for n in p["notes"] if n["signed_by"]]
        self.assertEqual(len(signed), 2)
        self.assertTrue(any(n["addenda"] for n in signed))

    def test_export_roundtrip(self):
        book = chart_demo.seed_charting()
        with tempfile.TemporaryDirectory() as td:
            path = chart_demo.export_charting(book,
                                              os.path.join(td, "c.json"))
            back = json.load(open(path))
        self.assertEqual(back["version"], chart_demo.DEMO_VERSION)


if __name__ == "__main__":
    unittest.main()
