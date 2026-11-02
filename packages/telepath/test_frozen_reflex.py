import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for pkg in ("telepath", "teleread"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", pkg)))

from telepath import (PathologyService, FrozenSectionLog,
                      DerivativeInventory, ReflexLog, TeleReadError,
                      FROZEN_TAT_MINUTES)
from teleread import ReaderRegistry


def mk_path_with_verified():
    readers = ReaderRegistry()
    readers.register("path-1", licence_id="MDCN-P1",
                     licence_expiry_day=10**9,
                     scopes=("histopathologist",))
    path = PathologyService(readers)
    path.register_specimen("SP-1", patient_id="p-1",
                           specimen_type="resection", site="colon",
                           collector="surg-1", facility="phc-1", day=0)
    for st in ("fixed", "processed", "sectioned", "stained",
               "scanned"):
        kw = {"fixation_hours": 12} if st == "processed" else {}
        path.advance("SP-1", st, day=0, by="lab-1", **kw)
    path.submit_for_reading("SP-1", case_id="PC-1", priority="routine",
                            referrer="surg-1", question="ca?",
                            artifact="wsi", day=0)
    path.reading.assign("PC-1", day=0, prefer="path-1")
    path.reading.draft_report("PC-1", reader_id="path-1",
                              findings="tumor", impression="adeno",
                              template_version="freeform@1", day=1)
    path.reading.verify_report("PC-1", reader_id="path-1", day=1)
    return path


class TestFrozenSection(unittest.TestCase):
    def test_clock_and_readback(self):
        log = FrozenSectionLog()
        log.request("FS-1", patient_id="p-1", site="breast lump",
                    surgeon="surg-1", facility="phc-1", day=0,
                    minute=600)
        out = log.report_result("FS-1", finding="malignant",
                                by="path-1", day=0, minute=622,
                                read_back_by="surg-1")
        self.assertEqual(out["tat_minutes"], 22)
        self.assertTrue(out["within_target"])

    def test_no_readback_no_result(self):
        log = FrozenSectionLog()
        log.request("FS-1", patient_id="p-1", site="breast",
                    surgeon="surg-1", facility="phc-1", day=0,
                    minute=600)
        with self.assertRaises(TeleReadError):
            log.report_result("FS-1", finding="benign", by="path-1",
                              day=0, minute=610, read_back_by="")

    def test_breach_and_concordance_denominators(self):
        log = FrozenSectionLog()
        log.request("FS-1", patient_id="p-1", site="a",
                    surgeon="s", facility="f", day=0, minute=0)
        log.request("FS-2", patient_id="p-2", site="b",
                    surgeon="surg-1", facility="f", day=0, minute=0)
        log.report_result("FS-1", finding="benign", by="path-1",
                          day=0, minute=FROZEN_TAT_MINUTES + 15,
                          read_back_by="surg-1")
        log.report_result("FS-2", finding="malignant", by="path-1",
                          day=0, minute=20, read_back_by="surg-1")
        log.correlate_permanent("FS-1", permanent_finding="benign",
                                by="path-1", day=3)
        log.correlate_permanent("FS-2", permanent_finding="atypical-defer",
                                by="path-1", day=3)
        rep = log.report()
        self.assertEqual(rep["tat_within_target"]["n"], 1)
        self.assertEqual(rep["tat_within_target"]["rate"], 0.5)
        self.assertEqual(rep["concordance"]["n"], 1)
        self.assertEqual(rep["concordance"]["of"], 2)

    def test_correlation_requires_result_and_never_rewrites(self):
        log = FrozenSectionLog()
        log.request("FS-1", patient_id="p-1", site="a",
                    surgeon="s", facility="f", day=0, minute=0)
        with self.assertRaises(TeleReadError):
            log.correlate_permanent("FS-1", permanent_finding="benign",
                                    by="p", day=1)
        log.report_result("FS-1", finding="benign", by="p", day=0,
                          minute=10, read_back_by="s")
        log.correlate_permanent("FS-1", permanent_finding="benign",
                                by="p", day=3)
        with self.assertRaises(TeleReadError):
            log.correlate_permanent("FS-1", permanent_finding="malignant",
                                    by="p", day=4)


class TestDerivativeInventory(unittest.TestCase):
    def test_block_slide_location_trail(self):
        path = mk_path_with_verified()
        inv = DerivativeInventory(path)
        inv.register_block("B1", specimen_id="SP-1",
                           location="paraffin-store-A", day=1,
                           by="lab-1")
        slides = inv.cut_slides("B1", n_slides=3, stain="h-and-e",
                                location="lab-bench-2", day=1,
                                by="lab-1")
        self.assertEqual(slides, ["B1-S1", "B1-S2", "B1-S3"])
        inv.move("B1-S1", to_location="scanner-1", day=2, by="lab-2")
        loc = inv.locate("B1-S1")
        self.assertEqual(loc["location"], "scanner-1")
        self.assertEqual(len(loc["trail"]), 2)  # bench -> scanner
        rep = inv.inventory_report()
        self.assertEqual(rep["blocks_per_specimen"]["SP-1"], 1)
        self.assertEqual(rep["slides"], 3)

    def test_unknown_specimen_and_bad_stain_refuse(self):
        path = mk_path_with_verified()
        inv = DerivativeInventory(path)
        with self.assertRaises(TeleReadError):
            inv.register_block("B2", specimen_id="SP-NOPE",
                               location="shelf", day=1, by="lab-1")
        inv.register_block("B3", specimen_id="SP-1", location="store",
                           day=1, by="lab-1")
        with self.assertRaises(TeleReadError):
            inv.cut_slides("B3", n_slides=1, stain="magic",
                           location="bench", day=1, by="lab-1")


class TestReflexLog(unittest.TestCase):
    def test_reflex_from_verified_lands_as_addendum(self):
        path = mk_path_with_verified()
        rx = ReflexLog(path.reading)
        rx.order("RX-1", case_id="PC-1", test="ihc-MLH1",
                 reason="Lynch screen", by="path-1", day=2)
        rx.record_result("RX-1", result="loss of expression",
                         by="path-1", day=5)
        rep = path.reading.reports["PC-1"]
        self.assertEqual(rep["state"], "addended")
        self.assertIn("ihc-MLH1: loss of expression",
                      rep["addenda"][0]["text"])

    def test_no_reflex_from_draft_and_no_empty_result(self):
        path = mk_path_with_verified()
        # a draft-only case refuses
        path.register_specimen("SP-2", patient_id="p-2",
                               specimen_type="biopsy", site="skin",
                               collector="d-1", facility="phc-1",
                               day=0)
        rx = ReflexLog(path.reading)
        with self.assertRaises(TeleReadError):
            rx.order("RX-2", case_id="PC-DOES-NOT-EXIST",
                     test="ihc-ER", reason="r", by="path-1", day=2)
        rx.order("RX-3", case_id="PC-1", test="ihc-ER",
                 reason="r", by="path-1", day=2)
        with self.assertRaises(TeleReadError):
            rx.record_result("RX-3", result="", by="path-1", day=3)

    def test_pending_is_overdue_first(self):
        path = mk_path_with_verified()
        rx = ReflexLog(path.reading)
        rx.order("RX-A", case_id="PC-1", test="ihc-ER", reason="r",
                 by="path-1", day=1)
        rx.order("RX-B", case_id="PC-1", test="ihc-PR", reason="r",
                 by="path-1", day=5)
        rx.record_result("RX-B", result="positive", by="path-1", day=6)
        pend = rx.pending(as_of=10)
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["reflex_id"], "RX-A")
        self.assertEqual(pend[0]["age_days"], 9)


if __name__ == "__main__":
    unittest.main()
