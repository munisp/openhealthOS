"""Tests for packages/anesthesia — the intra-operative anaesthesia record."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from anaesthesia import AnaesthesiaRecord, Error, AGENTS, register_agent  # noqa: E402


def make():
    rec = AnaesthesiaRecord("ANA-1", "CASE-7", "Pax-1",
                            anaesthetist="dr-anes-1", day=1)
    rec.assess_preop(asa="II", mallampati="II", fasting_hours=8,
                     elective=True, airway_plan="direct laryngoscopy",
                     by="dr-anes-1")
    return rec


class TestPreopGate(unittest.TestCase):
    def test_no_induction_without_assessment(self):
        rec = AnaesthesiaRecord("A", "C", "P", anaesthetist="dr", day=1)
        with self.assertRaises(Error):
            rec.induce(minute=0, anaesthesia_type="general")

    def test_unfasted_elective_refused(self):
        rec = AnaesthesiaRecord("A", "C", "P", anaesthetist="dr", day=1)
        with self.assertRaises(Error):
            rec.assess_preop(asa="I", mallampati="I", fasting_hours=3,
                             elective=True, airway_plan="x", by="dr")
        rec.assess_preop(asa="I", mallampati="I", fasting_hours=3,
                         elective=False, airway_plan="x", by="dr")

    def test_other_hand_refused(self):
        rec = AnaesthesiaRecord("A", "C", "P", anaesthetist="dr", day=1)
        with self.assertRaises(Error):
            rec.assess_preop(asa="I", mallampati="I", fasting_hours=8,
                             elective=True, airway_plan="x", by="nurse")

    def test_bad_grades_refused(self):
        rec = AnaesthesiaRecord("A", "C", "P", anaesthetist="dr", day=1)
        with self.assertRaises(Error):
            rec.assess_preop(asa="VII", mallampati="I", fasting_hours=8,
                             elective=True, airway_plan="x", by="dr")
        with self.assertRaises(Error):
            rec.assess_preop(asa="I", mallampati="X", fasting_hours=8,
                             elective=True, airway_plan="x", by="dr")


class TestIntraop(unittest.TestCase):
    def test_full_timeline(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="general")
        rec.administer_agent("propofol", "150mg", minute=0,
                             by="dr-anes-1", witness="nurse-ola")
        rec.record_event("intubated, ETT 7.5", minute=3)
        rec.record_observation("heart-rate", 78, minute=5)
        rec.record_observation("spo2", 99, minute=5)
        rec.record_observation("etco2", 35, minute=5)
        rec.record_event("knife to skin", minute=15)
        d = rec.close(minute=90, disposition="pacu",
                      handed_over_to="nurse-pacu-1", by="dr-anes-1")
        self.assertEqual(d["disposition"], "pacu")
        out = rec.as_dict()
        self.assertEqual(len(out["sha256"]), 64)
        self.assertEqual(len([e for e in out["timeline"]
                              if e["kind"] == "observation"]), 3)

    def test_implausible_observation_refused(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="spinal")
        with self.assertRaises(Error):
            rec.record_observation("spo2", 140, minute=5)

    def test_unknown_agent_refused_then_registered(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="general")
        with self.assertRaises(Error):
            rec.administer_agent("magic-gas", "1 puff", minute=1,
                                 by="dr-anes-1")
        register_agent("magic-gas", "volatile", False)
        rec.administer_agent("magic-gas", "1 puff", minute=1,
                             by="dr-anes-1")
        del AGENTS["magic-gas"]  # test hygiene

    def test_high_alert_agent_needs_witness(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="general")
        with self.assertRaises(Error):
            rec.administer_agent("fentanyl", "100mcg", minute=1,
                                 by="dr-anes-1")
        rec.administer_agent("fentanyl", "100mcg", minute=1,
                             by="dr-anes-1", witness="nurse-ola")

    def test_only_anaesthetist_records_agents(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="general")
        with self.assertRaises(Error):
            rec.administer_agent("atracurium", "25mg", minute=5,
                                 by="surgeon-1")

    def test_no_double_induction(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="general")
        with self.assertRaises(Error):
            rec.induce(minute=5, anaesthesia_type="spinal")


class TestEmergence(unittest.TestCase):
    def test_close_requires_disposition_and_recipient(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="general")
        with self.assertRaises(Error):
            rec.close(minute=60, disposition="home", handed_over_to="x",
                      by="dr-anes-1")
        with self.assertRaises(Error):
            rec.close(minute=60, disposition="pacu", handed_over_to="",
                      by="dr-anes-1")
        rec.close(minute=60, disposition="icu", handed_over_to="dr-icu",
                  by="dr-anes-1")
        with self.assertRaises(Error):
            rec.record_observation("heart-rate", 70, minute=65)

    def test_closed_is_final(self):
        rec = make()
        rec.induce(minute=0, anaesthesia_type="sedation")
        rec.close(minute=30, disposition="ward", handed_over_to="nurse-w",
                  by="dr-anes-1")
        with self.assertRaises(Error):
            rec.close(minute=35, disposition="ward",
                      handed_over_to="nurse-w", by="dr-anes-1")


if __name__ == "__main__":
    unittest.main()
