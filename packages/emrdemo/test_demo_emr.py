"""Tests for packages/emrdemo — deterministic demo exporters."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from demo_emr import (  # noqa: E402
    seed_inpatient, seed_specialty, export_all, VERSION,
)


class TestDeterminism(unittest.TestCase):
    def test_same_seed_same_bytes(self):
        a = seed_inpatient("ng", seed=42)
        b = seed_inpatient("ng", seed=42)
        self.assertEqual(a["sha256"], b["sha256"])

    def test_locales_differ(self):
        ng = seed_inpatient("ng", seed=42)
        cv = seed_inpatient("cv", seed=42)
        self.assertNotEqual(ng["sha256"], cv["sha256"])
        self.assertEqual(ng["country"], "Nigeria")
        self.assertEqual(cv["country"], "Cabo Verde")
        self.assertEqual(ng["currency"], "NGN")
        self.assertEqual(cv["currency"], "CVE")

    def test_no_locale_bleed(self):
        cv = seed_inpatient("cv", seed=42)
        text = json.dumps(cv, ensure_ascii=False)
        self.assertNotIn("Okafor", text)
        self.assertNotIn("Lagos", text)
        self.assertNotIn("₦", text)


class TestInpatientPayload(unittest.TestCase):
    def setUp(self):
        self.p = seed_inpatient("ng", seed=42)

    def test_mar_gates_visible(self):
        audit = self.p["mar_audit"]
        self.assertEqual(audit["given"], 1)
        self.assertEqual(audit["refused"], 1)
        self.assertEqual(audit["scheduled"], 1)  # overdue on the sheet

    def test_flowsheet_correction_cites(self):
        temps = [r for r in self.p["flowsheet_rows"]
                 if r["vital"] == "temperature"]
        self.assertEqual(len(temps), 2)
        self.assertEqual(temps[1]["amends"], temps[0]["row_id"])

    def test_handoff_countersigned(self):
        self.assertTrue(self.p["handoff"]["countersigned"])
        self.assertNotEqual(self.p["handoff"]["from"],
                            self.p["handoff"]["to"])

    def test_ward_report_flags_never_recorded(self):
        overdue = self.p["ward_report"]["overdue_vitals"]
        # p1 recorded 3 vitals, p2 recorded none — both are flagged,
        # but p2 must show "never-recorded" across the board
        p2 = [o for o in overdue if o["patient_ref"].endswith("-0002")]
        self.assertEqual(len(p2), 1)
        self.assertTrue(all(g == "never-recorded"
                            for g in p2[0]["gaps"].values()))

    def test_anaesthesia_complete(self):
        ana = self.p["anaesthesia"]
        self.assertEqual(ana["closed"]["disposition"], "pacu")
        self.assertEqual(len(ana["sha256"]), 64)

    def test_growth_graded(self):
        g = self.p["growth"]
        self.assertIn(g["wfa"]["band"], ("severe", "moderate", "normal"))
        self.assertIsNotNone(g["wfh"])  # height was present


class TestSpecialtyPayload(unittest.TestCase):
    def setUp(self):
        self.p = seed_specialty("cv", seed=42)

    def test_dental_terminal_state(self):
        d = self.p["dental"]
        self.assertEqual(d["teeth"][46]["condition"], "extracted")
        self.assertEqual(d["counts"]["extracted"], 1)

    def test_oncology_gate_refusal_visible(self):
        o = self.p["oncology"]
        self.assertIsNotNone(o["gate_refusal"])
        self.assertIn("neutrophils", o["gate_refusal"])
        self.assertEqual(o["cycles_planned"], 1)  # cycle 2 refused

    def test_oncology_dose_capped(self):
        doses = self.p["oncology"]["cycle_1_doses"]
        vcr = [d for d in doses if d["drug"] == "vincristine"][0]
        self.assertTrue(vcr["capped"])
        self.assertEqual(vcr["administered_mg"], 2.0)

    def test_cssd_quarantine_visible(self):
        c = self.p["cssd"]
        self.assertEqual(c["quarantined"], 1)
        self.assertEqual(c["usable"], 1)

    def test_dietetics_manifest(self):
        d = self.p["dietetics"]
        self.assertEqual(d["screen"]["recommended_diet"], "f-75")
        self.assertEqual(d["manifest"]["portions_by_diet"]["f-75"], 6)

    def test_certificates_verify(self):
        c = self.p["certificates"]
        self.assertTrue(c["verify_sick_note"][0])
        self.assertEqual(c["register"]["by_type"]["sick-note"], 1)

    def test_problem_list(self):
        pl = self.p["problem_list"]
        self.assertEqual(pl["counts"]["total"], 3)
        self.assertEqual(pl["counts"]["uncoded"], 1)  # malaria, stated


class TestExport(unittest.TestCase):
    def test_export_all_four_payloads(self):
        with tempfile.TemporaryDirectory() as td:
            paths = export_all(td, seed=42)
            self.assertEqual(len(paths), 4)
            for p in paths:
                with open(p, encoding="utf-8") as fh:
                    payload = json.load(fh)
                self.assertEqual(payload["version"], VERSION)
                self.assertEqual(len(payload["sha256"]), 64)
            names = sorted(os.path.basename(p) for p in paths)
            self.assertEqual(names, ["inpatient.cv.json",
                                     "inpatient.ng.json",
                                     "specialty.cv.json",
                                     "specialty.ng.json"])

    def test_export_is_reproducible(self):
        with tempfile.TemporaryDirectory() as t1, \
                tempfile.TemporaryDirectory() as t2:
            export_all(t1, seed=42)
            export_all(t2, seed=42)
            for name in ("inpatient.ng.json", "specialty.cv.json"):
                with open(os.path.join(t1, name), "rb") as f1, \
                        open(os.path.join(t2, name), "rb") as f2:
                    self.assertEqual(f1.read(), f2.read())


if __name__ == "__main__":
    unittest.main()
