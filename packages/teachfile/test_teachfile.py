import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for pkg in ("teachfile", "teleread", "pacs"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", pkg)))

from teachfile import TeachingLibrary, TeachFileError, TEACHFILE_VERSION
from teleread import DomainConfig, ReadingService, ReaderRegistry
from pacs import StudyRegistry, PACSError


def make_stack():
    cfg = DomainConfig("radiology", scopes=("chest",), sla_hours={"routine": 48})
    readers = ReaderRegistry()
    readers.register("rad-1", licence_id="L1", licence_expiry_day=10**9,
                     scopes=("chest",))
    svc = ReadingService(cfg, readers)
    pacs = StudyRegistry()
    return svc, pacs


def verified_case(svc, pacs, *, case_id="CX-1", study_uid="1.2.3",
                  patient_id="p-1", burned=False, day=10):
    pacs.register_study(study_uid=study_uid, accession=f"A-{study_uid}",
                        patient_id=patient_id, modality="CR",
                        exam_protocol="chest-pa", priority="routine",
                        facility="phc-1", day=day, instances=2,
                        burned_in_annotations=burned)
    svc.order(case_id, priority="routine", question="TB?",
              facility="phc-1", referrer="dr-a", day=day)
    svc.assign(case_id, day=day, prefer="rad-1")
    svc.draft_report(case_id, reader_id="rad-1",
                     findings="right upper lobe cavitation",
                     impression="active pulmonary TB",
                     template_version="cxr@1.0.0", day=day)
    svc.verify_report(case_id, reader_id="rad-1", day=day + 1)
    return case_id, study_uid


class TestContribution(unittest.TestCase):
    def test_verified_case_contributes_anonymized(self):
        svc, pacs = make_stack()
        cid, uid = verified_case(svc, pacs)
        lib = TeachingLibrary(svc, pacs)
        out = lib.contribute(cid, uid, title="Typical post-primary TB",
                             consent_basis="explicit-consent",
                             contributed_by="rad-1", day=20,
                             tags=("tb", "cavitation"))
        e = lib.library()[0]
        self.assertEqual(out["teaching_id"], e["teaching_id"])
        self.assertNotIn("patient_id", e)
        self.assertNotIn("p-1", str(e))
        self.assertNotIn("day", {k for k in e if k == "day"})
        self.assertEqual(e["case_age_days"], 10)  # relative, not absolute
        self.assertEqual(e["version"], TEACHFILE_VERSION)

    def test_draft_report_cannot_teach(self):
        svc, pacs = make_stack()
        cfg_case = "CX-2"
        pacs.register_study(study_uid="9.9.9", accession="A-9",
                            patient_id="p-2", modality="CR",
                            exam_protocol="chest-pa", priority="routine",
                            facility="phc-1", day=10)
        svc.order(cfg_case, priority="routine", question="TB?",
                  facility="phc-1", referrer="dr-a", day=10)
        svc.assign(cfg_case, day=10, prefer="rad-1")
        svc.draft_report(cfg_case, reader_id="rad-1", findings="x",
                         impression="y", template_version="cxr@1.0.0",
                         day=10)
        lib = TeachingLibrary(svc, pacs)
        with self.assertRaises(TeachFileError):
            lib.contribute(cfg_case, "9.9.9", title="draft",
                           consent_basis="explicit-consent",
                           contributed_by="rad-1", day=20)

    def test_bad_consent_basis_refused(self):
        svc, pacs = make_stack()
        cid, uid = verified_case(svc, pacs)
        lib = TeachingLibrary(svc, pacs)
        with self.assertRaises(TeachFileError):
            lib.contribute(cid, uid, title="x", consent_basis="assumed",
                           contributed_by="rad-1", day=20)

    def test_burned_in_pixels_refused_at_pacs_gate(self):
        svc, pacs = make_stack()
        cid, uid = verified_case(svc, pacs, case_id="CX-B",
                                 study_uid="7.7.7", patient_id="p-b",
                                 burned=True)
        lib = TeachingLibrary(svc, pacs)
        with self.assertRaises(PACSError):
            lib.contribute(cid, uid, title="burned",
                           consent_basis="anonymized-irreversible",
                           contributed_by="rad-1", day=20)

    def test_patient_id_in_free_text_refused(self):
        svc, pacs = make_stack()
        cid, uid = verified_case(svc, pacs)
        lib = TeachingLibrary(svc, pacs)
        with self.assertRaises(TeachFileError):
            lib.contribute(cid, uid, title="TB case of patient p-1",
                           consent_basis="explicit-consent",
                           contributed_by="rad-1", day=20)

    def test_no_double_contribution(self):
        svc, pacs = make_stack()
        cid, uid = verified_case(svc, pacs)
        lib = TeachingLibrary(svc, pacs)
        lib.contribute(cid, uid, title="t", consent_basis="explicit-consent",
                       contributed_by="rad-1", day=20)
        with self.assertRaises(TeachFileError):
            lib.contribute(cid, uid, title="t2",
                           consent_basis="explicit-consent",
                           contributed_by="rad-1", day=21)


class TestUsageAndRetraction(unittest.TestCase):
    def test_usage_report_has_denominators(self):
        svc, pacs = make_stack()
        cid, uid = verified_case(svc, pacs)
        cid2, uid2 = verified_case(svc, pacs, case_id="CX-2",
                                   study_uid="4.4.4", patient_id="p-2")
        lib = TeachingLibrary(svc, pacs)
        t1 = lib.contribute(cid, uid, title="one",
                            consent_basis="explicit-consent",
                            contributed_by="rad-1", day=20)["teaching_id"]
        lib.contribute(cid2, uid2, title="two",
                       consent_basis="anonymized-irreversible",
                       contributed_by="rad-1", day=20)
        lib.record_use(t1, by="student-1", day=21, purpose="training")
        rep = lib.usage_report()
        self.assertEqual(rep["contributed"], 2)
        self.assertEqual(rep["total_uses"], 1)
        self.assertEqual(rep["entries_never_used"]["n"], 1)
        self.assertEqual(rep["entries_never_used"]["rate"], 0.5)

    def test_retraction_is_append_only_and_blocks_use(self):
        svc, pacs = make_stack()
        cid, uid = verified_case(svc, pacs)
        lib = TeachingLibrary(svc, pacs)
        tid = lib.contribute(cid, uid, title="t",
                             consent_basis="explicit-consent",
                             contributed_by="rad-1", day=20)["teaching_id"]
        lib.retract(tid, by="rad-1", day=30, reason="consent-withdrawn")
        self.assertEqual(lib.library(), [])  # hidden by default
        self.assertEqual(len(lib.library(include_retracted=True)), 1)
        with self.assertRaises(TeachFileError):
            lib.record_use(tid, by="student-1", day=31, purpose="training")
        self.assertEqual(lib.usage_report()["retracted"], 1)
        self.assertEqual(lib.retractions[0]["reason"], "consent-withdrawn")


if __name__ == "__main__":
    unittest.main()
