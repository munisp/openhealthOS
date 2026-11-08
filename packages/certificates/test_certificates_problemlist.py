"""Tests for packages/certificates and packages/problemlist."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "problemlist"))

from certificates import CertificateRegistry, Error as CertError  # noqa: E402
from problemlist import ProblemList, Error as ProbError  # noqa: E402


class TestCertificates(unittest.TestCase):
    def setUp(self):
        self.reg = CertificateRegistry("FAC-1")

    def test_sick_note_lifecycle(self):
        c = self.reg.issue_sick_note("Pax-1", issued_by="dr-eze", day=1,
                                     days_off=7,
                                     diagnosis_stated="malaria")
        self.assertEqual(c.body["days_off"], 7)
        ok, msg = self.reg.verify(c.cert_id, c.fingerprint())
        self.assertTrue(ok, msg)

    def test_sick_note_beyond_90_refused(self):
        with self.assertRaises(CertError):
            self.reg.issue_sick_note("Pax-1", issued_by="dr-eze", day=1,
                                     days_off=365, diagnosis_stated="x")

    def test_sick_note_needs_diagnosis(self):
        with self.assertRaises(CertError):
            self.reg.issue_sick_note("Pax-1", issued_by="dr-eze", day=1,
                                     days_off=7, diagnosis_stated="")

    def test_referral_completeness(self):
        c = self.reg.issue_referral(
            "Pax-1", issued_by="dr-eze", day=1, to_specialty="cardiology",
            to_facility="LUTH", clinical_question="? HOCM",
            summary="exertional syncope, loud murmur")
        self.assertEqual(c.cert_type, "referral-letter")
        with self.assertRaises(CertError):
            self.reg.issue_referral(
                "Pax-1", issued_by="dr-eze", day=1, to_specialty="",
                to_facility="LUTH", clinical_question="q", summary="s")

    def test_verification_catches_alteration(self):
        c = self.reg.issue_sick_note("Pax-1", issued_by="dr-eze", day=1,
                                     days_off=7, diagnosis_stated="flu")
        ok, msg = self.reg.verify(c.cert_id, "tampered-hash")
        self.assertFalse(ok)
        self.assertIn("altered", msg)

    def test_unknown_cert_refused(self):
        ok, _ = self.reg.verify("NONEXISTENT", "x")
        self.assertFalse(ok)

    def test_register_snapshot(self):
        self.reg.issue_sick_note("P1", issued_by="dr", day=1, days_off=3,
                                 diagnosis_stated="x")
        self.reg.issue_fit_note("P2", issued_by="dr", day=1,
                                statement="fit for work")
        snap = self.reg.register_snapshot()
        self.assertEqual(snap["by_type"]["sick-note"], 1)
        self.assertEqual(snap["by_type"]["fit-note"], 1)
        self.assertEqual(len(snap["sha256"]), 64)


class TestProblemList(unittest.TestCase):
    def setUp(self):
        self.pl = ProblemList("Pax-1")

    def test_add_coded_and_uncoded(self):
        p1 = self.pl.add(title="Hypertension", icd10="I10",
                         onset_day=1, recorded_by="dr-eze", day=1)
        p2 = self.pl.add(title="Chronic back pain", onset_day=1,
                         recorded_by="dr-eze", day=1)
        self.assertFalse(p1.uncoded)
        self.assertTrue(p2.uncoded)

    def test_bad_icd10_refused(self):
        with self.assertRaises(ProbError):
            self.pl.add(title="x", icd10="not-a-code", onset_day=1,
                        recorded_by="dr", day=1)

    def test_states_with_reasons(self):
        p = self.pl.add(title="T2DM", icd10="E11", onset_day=1,
                        recorded_by="dr-eze", day=1)
        with self.assertRaises(ProbError):
            p.resolve(by="dr-eze", day=100, reason="")
        p.resolve(by="dr-eze", day=100, reason="remission after bariatric")
        self.assertEqual(p.state, "resolved")
        p.recur(by="dr-eze", day=200, reason="weight regain, HbA1c 8.1")
        self.assertEqual(p.state, "recurrence")

    def test_active_problem_cannot_recur(self):
        p = self.pl.add(title="HTN", icd10="I10", onset_day=1,
                        recorded_by="dr", day=1)
        with self.assertRaises(ProbError):
            p.recur(by="dr", day=2, reason="x")

    def test_chronicity_computed(self):
        self.pl.add(title="HTN", icd10="I10", onset_day=1,
                    recorded_by="dr", day=1)
        self.pl.add(title="Recent ankle sprain", icd10="S93.4",
                    onset_day=95, recorded_by="dr", day=95)
        chronic = self.pl.chronic(day=100)
        self.assertEqual(len(chronic), 1)
        self.assertEqual(chronic[0].title, "HTN")

    def test_snapshot_checksummed(self):
        self.pl.add(title="HTN", icd10="I10", onset_day=1,
                    recorded_by="dr", day=1)
        self.pl.add(title="Asthma", icd10="J45", onset_day=50,
                    recorded_by="dr", day=50)
        snap = self.pl.snapshot(day=200)
        self.assertEqual(snap["counts"]["total"], 2)
        self.assertEqual(snap["counts"]["chronic"], 2)
        self.assertEqual(len(snap["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
