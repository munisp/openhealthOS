import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "teleread"))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from telepath import PathologyService


class TestTelepath(unittest.TestCase):
    def setUp(self):
        self.readers = ReaderRegistry()
        self.readers.register("path-1", licence_id="MDCN-P1",
                              licence_expiry_day=400,
                              scopes=("histopathologist",))
        self.svc = PathologyService(self.readers)
        self.svc.register_specimen("SP1", patient_id="p1",
                                   specimen_type="biopsy",
                                   site="cervix", collector="nurse-1",
                                   facility="phc-1", day=0)

    def test_custody_is_sequential(self):
        with self.assertRaises(TeleReadError):
            self.svc.advance("SP1", "scanned", day=0, by="lab-1")
        self.svc.advance("SP1", "fixed", day=0, by="lab-1")

    def test_underfixed_refused(self):
        self.svc.advance("SP1", "fixed", day=0, by="lab-1")
        with self.assertRaises(TeleReadError):
            self.svc.advance("SP1", "processed", day=1, by="lab-1",
                             fixation_hours=2)
        self.svc.advance("SP1", "processed", day=1, by="lab-1",
                         fixation_hours=12)

    def test_only_scanned_enters_queue(self):
        with self.assertRaises(TeleReadError):
            self.svc.submit_for_reading(
                "SP1", case_id="C1", priority="urgent", question="?ca",
                referrer="mo-1", day=1, artifact="wsi")
        for stage in ("fixed", "processed", "sectioned", "stained",
                      "scanned"):
            kw = {"fixation_hours": 12} if stage == "processed" else {}
            self.svc.advance("SP1", stage, day=1, by="lab-1", **kw)
        out = self.svc.submit_for_reading(
            "SP1", case_id="C1", priority="urgent", question="?ca",
            referrer="mo-1", day=1, artifact="wsi")
        self.assertEqual(out["scope"], "histopathologist")
        self.assertEqual(out["meta"]["site"], "cervix")

    def test_cytology_routes_to_cytopathologist(self):
        self.svc.register_specimen("SP2", patient_id="p2",
                                   specimen_type="cytology-fna",
                                   site="thyroid", collector="mo-1",
                                   facility="phc-2", day=0)
        for stage in ("fixed", "processed", "sectioned", "stained",
                      "scanned"):
            kw = {"fixation_hours": 12} if stage == "processed" else {}
            self.svc.advance("SP2", stage, day=1, by="lab-1", **kw)
        out = self.svc.submit_for_reading(
            "SP2", case_id="C2", priority="routine", question="?hashimoto",
            referrer="mo-1", day=1, artifact="wsi")
        self.assertEqual(out["scope"], "cytopathologist")

    def test_inadequate_is_a_finding(self):
        self.svc.reject_inadequate("SP1", day=1, by="path-1",
                                   reason="crush artefact")
        rate = self.svc.adequacy_rate()
        self.assertEqual((rate["specimens"], rate["inadequate"]), (1, 1))

    def test_engine_critical_escalation(self):
        for stage in ("fixed", "processed", "sectioned", "stained",
                      "scanned"):
            kw = {"fixation_hours": 12} if stage == "processed" else {}
            self.svc.advance("SP1", stage, day=1, by="lab-1", **kw)
        r = self.svc.reading
        r.order("C1", priority="urgent", question="?ca", facility="phc-1",
                referrer="mo-1", day=1, scope="histopathologist",
                artifact="wsi")
        r.assign("C1", day=1, prefer="path-1")
        r.draft_report("C1", reader_id="path-1", findings="invasive SCC",
                       impression="malignant", day=2,
                       critical_findings=("malignant",),
                       template_version="histo-v1")
        r.verify_report("C1", reader_id="path-1", day=2)
        self.assertEqual(len(r.unacknowledged_criticals(3)), 1)


if __name__ == "__main__":
    unittest.main()
