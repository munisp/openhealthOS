#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "contacttrace"))
from ringops import Ring, RingError  # noqa: E402
from contacttrace import ContactGraph  # noqa: E402

def graph():
    g = ContactGraph()
    g.add_case("CASE-1", "2026-10-20")
    g.add_contact("CASE-1", "C-01", "2026-10-07")
    g.add_contact("CASE-1", "C-02", "2026-10-07")
    g.checkin("C-01", "2026-10-12", symptomatic=True)
    g.add_case("CASE-2", "2026-10-12", source_contact_id="C-01")
    g.add_contact("CASE-2", "C-10", "2026-10-12")
    return g

class TestRing(unittest.TestCase):
    def test_ring_generated_from_graph(self):
        r = Ring.from_graph("R-1", "CASE-1", graph())
        self.assertEqual(set(r.people), {"C-01", "C-02"})

    def test_second_ring_opt_in_only(self):
        g = graph()
        r1 = Ring.from_graph("R-1", "CASE-1", g)
        self.assertNotIn("C-10", r1.people)
        r2 = Ring.from_graph("R-2", "CASE-1", g, include_second_ring=True)
        self.assertIn("C-10", r2.people)
        self.assertEqual(r2.people["C-10"]["ring"], 2)

    def test_dose_requires_consent(self):
        r = Ring.from_graph("R-1", "CASE-1", graph())
        with self.assertRaises(RingError):
            r.dose("C-01", "2026-10-21", "vacc-1")
        r.consent("C-01", "2026-10-21")
        r.dose("C-01", "2026-10-21", "vacc-1")
        self.assertEqual(r.people["C-01"]["state"], "dosed")

    def test_decline_requires_reason_and_counts(self):
        r = Ring.from_graph("R-1", "CASE-1", graph())
        with self.assertRaises(RingError):
            r.decline("C-02", " ", "2026-10-21")
        r.decline("C-02", "religious objection", "2026-10-21")
        c = r.completeness()
        self.assertEqual(c["declined"], 1)
        self.assertEqual(c["completeness"], 0.5)

    def test_aefi_only_after_dose_and_flagged(self):
        r = Ring.from_graph("R-1", "CASE-1", graph())
        with self.assertRaises(RingError):
            r.record_aefi("C-02", "fever", "2026-10-21")
        r.consent("C-01", "2026-10-21")
        r.dose("C-01", "2026-10-21", "vacc-1")
        r.record_aefi("C-01", "injection-site swelling", "2026-10-22")
        self.assertTrue(r.people["C-01"]["followup_needed"])
        self.assertEqual(r.completeness()["aefi_reports"], 1)

    def test_outsiders_and_empty_rings_refused(self):
        r = Ring.from_graph("R-1", "CASE-1", graph())
        with self.assertRaises(RingError):
            r.consent("C-99", "2026-10-21")
        with self.assertRaises(RingError):
            Ring("R-x", "CASE-9", [])

if __name__ == "__main__":
    unittest.main()
