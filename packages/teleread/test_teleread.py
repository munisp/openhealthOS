import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from teleread import (DomainConfig, ReaderRegistry, ReadingService,
                      TeleReadError)

CFG = DomainConfig("ecg", scopes=("cardiac-reader",),
                   critical_findings=("stemi", "complete-heart-block"),
                   artifact_kinds=("12-lead",))


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.readers = ReaderRegistry()
        self.readers.register("r1", licence_id="L1", licence_expiry_day=400,
                              scopes=("cardiac-reader",))
        self.readers.register("r2", licence_id="L2", licence_expiry_day=400,
                              scopes=("cardiac-reader",))
        self.svc = ReadingService(CFG, self.readers)
        self.svc.order("C1", priority="stat", question="?stemi",
                       facility="phc-1", referrer="nurse-1", day=0,
                       artifact="12-lead")

    def test_config_fail_closed(self):
        with self.assertRaises(TeleReadError):
            DomainConfig("", scopes=("x",))
        with self.assertRaises(TeleReadError):
            DomainConfig("x", scopes=())

    def test_order_fail_closed(self):
        with self.assertRaises(TeleReadError):
            self.svc.order("C2", priority="whenever", question="?",
                           facility="f", referrer="r", day=0)
        with self.assertRaises(TeleReadError):
            self.svc.order("C2", priority="stat", question="",
                           facility="f", referrer="r", day=0)
        with self.assertRaises(TeleReadError):
            self.svc.order("C2", priority="stat", question="?",
                           facility="f", referrer="r", day=0,
                           artifact="holter")
        with self.assertRaises(TeleReadError):
            self.svc.order("C2", priority="stat", question="?",
                           facility="f", referrer="r", day=0,
                           scope="neuro")
        with self.assertRaises(TeleReadError):
            self.svc.order("C1", priority="stat", question="?",
                           facility="f", referrer="r", day=0)

    def test_assignment_scope_and_expiry(self):
        self.readers.register("stale", licence_id="L9",
                              licence_expiry_day=5,
                              scopes=("cardiac-reader",))
        out = self.svc.assign("C1", day=0)
        self.assertIn(out["assigned_to"], ("r1", "r2", "stale"))
        with self.assertRaises(TeleReadError):
            self.svc.assign("C1", day=0)  # already assigned
        self.svc.order("C2", priority="routine", question="?", facility="f",
                       referrer="r", day=0)
        with self.assertRaises(TeleReadError):
            self.svc.assign("C2", day=100, candidates=["stale"])

    def test_workload_balances(self):
        self.svc.order("C2", priority="routine", question="?", facility="f",
                       referrer="r", day=0)
        a1 = self.svc.assign("C1", day=0)["assigned_to"]
        a2 = self.svc.assign("C2", day=0)["assigned_to"]
        self.assertNotEqual(a1, a2)  # least-loaded gets the second

    def test_worklist_stat_first_overdue_surfaced(self):
        self.svc.order("C2", priority="routine", question="?", facility="f",
                       referrer="r", day=0)
        self.svc.assign("C1", day=0, prefer="r1")
        self.svc.assign("C2", day=0, prefer="r1")
        wl = self.svc.worklist("r1", day=4)
        self.assertEqual(wl[0]["case_id"], "C1")
        self.assertTrue(wl[1]["overdue"])

    def test_session_gate(self):
        with self.assertRaises(TeleReadError):
            self.svc.open_session("r2", "C1", day=0, location="lagos")
        self.svc.assign("C1", day=0, prefer="r1")
        s = self.svc.open_session("r1", "C1", day=0, location="lagos")
        self.assertEqual(s["domain"], "ecg")

    def _verified(self):
        self.svc.assign("C1", day=0, prefer="r1")
        self.svc.draft_report("C1", reader_id="r1", findings="f",
                              impression="i", day=0,
                              critical_findings=("stemi",),
                              template_version="ecg-v1")
        return self.svc.verify_report("C1", reader_id="r1", day=0)

    def test_report_lifecycle(self):
        self.svc.assign("C1", day=0, prefer="r1")
        with self.assertRaises(TeleReadError):
            self.svc.draft_report("C1", reader_id="r2", findings="f",
                                  impression="i", day=0,
                                  template_version="ecg-v1")
        with self.assertRaises(TeleReadError):
            self.svc.draft_report("C1", reader_id="r1", findings="f",
                                  impression="i", day=0,
                                  critical_findings=("alien",),
                                  template_version="ecg-v1")
        self.svc.draft_report("C1", reader_id="r1", findings="f",
                              impression="i", day=0,
                              template_version="ecg-v1")
        v = self.svc.verify_report("C1", reader_id="r1", day=0)
        self.assertEqual(v["state"], "verified")
        a = self.svc.add_addendum("C1", reader_id="r1", day=1,
                                  text="measurement fix", reason="caliper")
        self.assertEqual(a["state"], "addended")
        self.assertEqual(a["findings"], "f")  # original untouched

    def test_critical_ack_ladder(self):
        self._verified()
        self.assertEqual(len(self.svc.escalations), 1)
        self.assertEqual(len(self.svc.unacknowledged_criticals(1)), 1)
        self.svc.acknowledge_critical("C1", day=1, by="mo-1")
        self.assertEqual(self.svc.unacknowledged_criticals(2), [])
        with self.assertRaises(TeleReadError):
            self.svc.acknowledge_critical("C1", day=2, by="mo-1")

    def test_expired_licence_cannot_verify(self):
        self.readers.register("short", licence_id="LS",
                              licence_expiry_day=3,
                              scopes=("cardiac-reader",))
        self.svc.order("C3", priority="urgent", question="?", facility="f",
                       referrer="r", day=0)
        self.svc.assign("C3", day=0, prefer="short")
        self.svc.draft_report("C3", reader_id="short", findings="f",
                              impression="i", day=1,
                              template_version="ecg-v1")
        with self.assertRaises(TeleReadError):
            self.svc.verify_report("C3", reader_id="short", day=10)

    def test_second_opinion_never_self(self):
        self._verified()
        out = self.svc.request_second_opinion("C1", requested_by="mo-1",
                                              day=1, reason="borderline")
        self.assertNotEqual(out["assigned_to"], "r1")
        with self.assertRaises(TeleReadError):
            self.svc.request_second_opinion("C1", requested_by="mo-1",
                                            day=1, reason="again")

    def test_transfer_gate(self):
        with self.assertRaises(TeleReadError):
            self.svc.transfer_case("C1", destination="hub", day=1,
                                   release_id=None)
        with self.assertRaises(TeleReadError):
            self.svc.transfer_case("C1", destination="hub", day=1,
                                   release_id="rel-1", bundle_hash="a",
                                   expected_hash="b")
        out = self.svc.transfer_case("C1", destination="hub", day=1,
                                     release_id="rel-1", bundle_hash="a",
                                     expected_hash="a")
        self.assertTrue(out["sent"])
        self.assertEqual(len(self.svc.transfers), 2)  # refusal logged

    def test_turnaround_and_peer_review(self):
        self._verified()
        tat = self.svc.turnaround(0)
        self.assertEqual(tat["by_priority"]["stat"]["n"], 1)
        with self.assertRaises(TeleReadError):
            self.svc.peer_review("C1", reviewer="r1", day=1, agree=True)
        self.svc.peer_review("C1", reviewer="r2", day=1, agree=False,
                             discrepancy="axis")
        self.assertEqual(self.svc.discrepancy_rate()["disagreements"], 1)

    def test_seam_only_verified(self):
        self.svc.assign("C1", day=0, prefer="r1")
        self.svc.draft_report("C1", reader_id="r1", findings="f",
                              impression="i", day=0,
                              template_version="ecg-v1")
        with self.assertRaises(TeleReadError):
            self.svc.result_ready_line("C1", day=0)
        self.svc.verify_report("C1", reader_id="r1", day=0)
        line = self.svc.result_ready_line("C1", day=0)
        self.assertIn('"kind": "ecg-result-ready"', line)


if __name__ == "__main__":
    unittest.main()
