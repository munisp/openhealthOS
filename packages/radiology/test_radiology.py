import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from radiology import (RadiologistRegistry, RadiologyWorklist,
                       RadiologyError)


class TestRadiology(unittest.TestCase):
    def setUp(self):
        self.reg = RadiologistRegistry()
        self.reg.register("rad-1", licence_id="MDCN-1",
                          licence_expiry_day=400,
                          subspecialties=("general", "chest"))
        self.reg.register("rad-2", licence_id="MDCN-2",
                          licence_expiry_day=400,
                          subspecialties=("neuro",))
        self.reg.register("rad-old", licence_id="MDCN-9",
                          licence_expiry_day=10)
        self.wl = RadiologyWorklist(self.reg)
        self.wl.order_study("S1", priority="stat",
                            clinical_question="?pneumothorax",
                            referring_facility="phc-1", referrer="nurse-1",
                            day=0, subspecialty="chest")

    def test_order_requires_question_and_enums(self):
        with self.assertRaises(RadiologyError):
            self.wl.order_study("S2", priority="stat",
                                clinical_question="",
                                referring_facility="f", referrer="r", day=0)
        with self.assertRaises(RadiologyError):
            self.wl.order_study("S2", priority="whenever",
                                clinical_question="?", referring_facility="f",
                                referrer="r", day=0)

    def test_assignment_matches_subspecialty(self):
        out = self.wl.assign("S1", day=0)
        self.assertEqual(out["assigned_to"], "rad-1")  # chest match

    def test_assignment_no_credentialed_refuses(self):
        self.wl.order_study("S3", priority="routine", clinical_question="?",
                            referring_facility="f", referrer="r", day=0)
        with self.assertRaises(RadiologyError):
            self.wl.assign("S3", day=100, candidates=["rad-old"])

    def test_worklist_stat_first_then_overdue(self):
        self.wl.order_study("S4", priority="routine", clinical_question="?",
                            referring_facility="f", referrer="r", day=0)
        self.wl.assign("S1", day=0)
        self.wl.assign("S4", day=0, prefer="rad-1")
        wl = self.wl.worklist("rad-1", day=4)   # S4 now past 72h SLA
        self.assertEqual(wl[0]["study_uid"], "S1")        # STAT first
        self.assertTrue(wl[1]["overdue"])                 # S4 overdue

    def test_session_requires_assignment(self):
        with self.assertRaises(RadiologyError):
            self.wl.open_session("rad-2", "S1", day=0, location="lagos")
        self.wl.assign("S1", day=0)
        s = self.wl.open_session("rad-1", "S1", day=0, location="lagos",
                                 network="offline")
        self.assertTrue(s["opened"])

    def test_report_lifecycle_and_addendum_cites(self):
        self.wl.assign("S1", day=0)
        with self.assertRaises(RadiologyError):
            self.wl.draft_report("S1", rad_id="rad-1", findings="f",
                                 impression="i", day=0,
                                 template_version="")
        rep = self.wl.draft_report("S1", rad_id="rad-1", findings="f",
                                   impression="i", day=0,
                                   critical_findings=("pneumothorax",),
                                   template_version="chest-ct-v3")
        self.assertEqual(rep["state"], "draft")
        v = self.wl.verify_report("S1", rad_id="rad-1", day=0)
        self.assertEqual(v["state"], "verified")
        # critical finding escalated; ack ladder works
        self.assertEqual(len(self.wl.escalations), 1)
        self.assertEqual(len(self.wl.unacknowledged_criticals(1)), 1)
        self.wl.acknowledge_critical("S1", day=1, by="mo-1")
        self.assertEqual(self.wl.unacknowledged_criticals(2), [])
        # correction is an addendum, never an edit
        a = self.wl.add_addendum("S1", rad_id="rad-1", day=1,
                                 text="size corrected", reason="measurement")
        self.assertEqual(a["state"], "addended")
        self.assertEqual(a["findings"], "f")   # original untouched

    def test_expired_licence_cannot_verify(self):
        self.wl.order_study("S5", priority="urgent", clinical_question="?",
                            referring_facility="f", referrer="r", day=0)
        self.reg.register("rad-3", licence_id="MDCN-3",
                          licence_expiry_day=5)
        self.wl.assign("S5", day=0, prefer="rad-3")
        self.wl.draft_report("S5", rad_id="rad-3", findings="f",
                             impression="i", day=1,
                             template_version="gen-v1")
        with self.assertRaises(RadiologyError):
            self.wl.verify_report("S5", rad_id="rad-3", day=10)

    def test_turnaround_denominators(self):
        self.wl.assign("S1", day=0)
        self.wl.draft_report("S1", rad_id="rad-1", findings="f",
                             impression="i", day=0,
                             template_version="chest-ct-v3")
        self.wl.verify_report("S1", rad_id="rad-1", day=0)
        tat = self.wl.turnaround(0)
        self.assertEqual(tat["by_priority"]["stat"]["n"], 1)
        self.assertEqual(tat["by_priority"]["stat"]["within_sla"], 1)

    def test_peer_review_rules(self):
        self.wl.assign("S1", day=0)
        self.wl.draft_report("S1", rad_id="rad-1", findings="f",
                             impression="i", day=0,
                             template_version="chest-ct-v3")
        self.wl.verify_report("S1", rad_id="rad-1", day=0)
        with self.assertRaises(RadiologyError):
            self.wl.peer_review("S1", reviewer="rad-1", day=1, agree=True)
        with self.assertRaises(RadiologyError):
            self.wl.peer_review("S1", reviewer="rad-2", day=1,
                                agree=False, discrepancy=None)
        self.wl.peer_review("S1", reviewer="rad-2", day=1, agree=False,
                            discrepancy="missed apical cap")
        dr = self.wl.discrepancy_rate()
        self.assertEqual((dr["reviewed"], dr["disagreements"]), (1, 1))

    def test_transfer_gated_by_release_and_hash(self):
        with self.assertRaises(RadiologyError):
            self.wl.transfer_study("S1", destination="ghana-hospital",
                                   day=1)
        with self.assertRaises(RadiologyError):
            self.wl.transfer_study("S1", destination="ghana-hospital",
                                   day=1, release_id="rel-1",
                                   bundle_hash="abc", expected_hash="xyz")
        out = self.wl.transfer_study("S1", destination="ghana-hospital",
                                     day=1, release_id="rel-1",
                                     bundle_hash="abc",
                                     expected_hash="abc")
        self.assertTrue(out["sent"])
        # refusal was logged as a finding
        self.assertEqual(len(self.wl.transfers), 2)

    def test_report_ready_seam_only_for_verified(self):
        self.wl.assign("S1", day=0)
        self.wl.draft_report("S1", rad_id="rad-1", findings="f",
                             impression="i", day=0,
                             template_version="chest-ct-v3")
        with self.assertRaises(RadiologyError):
            self.wl.report_ready_line("S1", day=0)
        self.wl.verify_report("S1", rad_id="rad-1", day=0)
        line = self.wl.report_ready_line("S1", day=0)
        self.assertIn('"kind": "report-ready"', line)
        self.assertIn('"facility": "phc-1"', line)


if __name__ == "__main__":
    unittest.main()
