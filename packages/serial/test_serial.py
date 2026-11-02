"""test_serial.py — trends, not snapshots."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("serial", "pacs", "reportkit"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from serial import (SerialError, SeriesTracker, priors, compare_ecg,  # noqa: E402
                    fetal_biometry, BIOMETRY_REF_VERSION)
from pacs import StudyRegistry  # noqa: E402
from reportkit import ReportKit  # noqa: E402


class TestMeasurements(unittest.TestCase):
    def setUp(self):
        self.st = SeriesTracker()

    def test_versions_never_edit(self):
        v1 = self.st.record_measurement("S1", lesion_id="L1",
                                        kind="long-axis", value_mm=22,
                                        day=0, by="rad-1")
        v2 = self.st.record_measurement("S1", lesion_id="L1",
                                        kind="long-axis", value_mm=24,
                                        day=0, by="rad-2")
        self.assertEqual((v1["version"], v2["version"]), (1, 2))

    def test_recist_progression_needs_absolute_floor(self):
        # 3mm → 5mm is +67% but only 2mm absolute: stable, not progression
        self.st.record_measurement("SA", lesion_id="L1", kind="long-axis",
                                   value_mm=3, day=0, by="r")
        self.st.record_measurement("SB", lesion_id="L1", kind="long-axis",
                                   value_mm=5, day=30, by="r")
        out = self.st.compare_lesion("SA", "SB", lesion_id="L1")
        self.assertEqual(out["flag"], "stable")
        # 25mm → 31mm is +24% and +6mm: progression
        self.st.record_measurement("SA", lesion_id="L2", kind="long-axis",
                                   value_mm=25, day=0, by="r")
        self.st.record_measurement("SB", lesion_id="L2", kind="long-axis",
                                   value_mm=31, day=30, by="r")
        out = self.st.compare_lesion("SA", "SB", lesion_id="L2")
        self.assertEqual(out["flag"], "progression")

    def test_response_flag(self):
        self.st.record_measurement("SA", lesion_id="L1", kind="long-axis",
                                   value_mm=40, day=0, by="r")
        self.st.record_measurement("SB", lesion_id="L1", kind="long-axis",
                                   value_mm=27, day=60, by="r")
        out = self.st.compare_lesion("SA", "SB", lesion_id="L1")
        self.assertEqual(out["flag"], "response")

    def test_missing_measurement_refuses(self):
        with self.assertRaises(SerialError):
            self.st.compare_lesion("SA", "SB", lesion_id="ghost")

    def test_orphan_and_implausible_refused(self):
        with self.assertRaises(SerialError):
            self.st.record_measurement("S1", lesion_id="", kind="k",
                                       value_mm=10, day=0, by="r")
        with self.assertRaises(SerialError):
            self.st.record_measurement("S1", lesion_id="L1", kind="k",
                                       value_mm=5000, day=0, by="r")
        with self.assertRaises(SerialError):
            self.st.record_measurement("S1", lesion_id="L1", kind="k",
                                       value_mm=10, day=0, by="")


class TestPriors(unittest.TestCase):
    def test_chain_oldest_first_filtered(self):
        reg = StudyRegistry()
        reg.register_study(study_uid="S3", accession="A3",
                           patient_id="p1", modality="CT",
                           exam_protocol="ct-chest", priority="routine",
                           facility="f", day=60, instances=100)
        reg.register_study(study_uid="S1", accession="A1",
                           patient_id="p1", modality="CT",
                           exam_protocol="ct-chest", priority="routine",
                           facility="f", day=0, instances=100)
        reg.register_study(study_uid="S2", accession="A2",
                           patient_id="p1", modality="US",
                           exam_protocol="us-abdo", priority="routine",
                           facility="f", day=30, instances=12)
        out = priors(reg, "p1", modality="CT")
        self.assertEqual([s["study_uid"] for s in out["studies"]],
                         ["S1", "S3"])
        self.assertEqual(priors(reg, "p2")["count"], 0)


class TestCompareECG(unittest.TestCase):
    def _report(self, rk, rid, qtc, rate=80, pr=160, qrs=90):
        rk.draft(rid, template_id="ecg-12lead@1.0.0",
                 answers={"rate_bpm": rate, "rhythm": "sinus",
                          "pr_ms": pr, "qrs_ms": qrs, "qtc_ms": qtc,
                          "findings": "x", "interpretation": "y",
                          "acuity": "routine"},
                 case_id=f"C-{rid}", author_id="rad-1", day=0)
        rk.sign(rid, signer_id="rad-1", day=1)
        return rk.reports[rid]

    def test_qtc_prolongation_flagged(self):
        rk = ReportKit(signing_key=b"k")
        a = self._report(rk, "R1", 420)
        b = self._report(rk, "R2", 485)
        out = compare_ecg(a, b)
        self.assertEqual(out["deltas"]["qtc_ms"]["delta"], 65)
        self.assertTrue(any("QTc +65" in f for f in out["flags"]))

    def test_new_conduction_block_flagged(self):
        rk = ReportKit(signing_key=b"k")
        a = self._report(rk, "R1", 400, qrs=100)
        b = self._report(rk, "R2", 400, qrs=130)
        out = compare_ecg(a, b)
        self.assertTrue(any("new QRS prolongation" in f
                            for f in out["flags"]))

    def test_unsigned_refused(self):
        rk = ReportKit(signing_key=b"k")
        a = self._report(rk, "R1", 420)
        rk.draft("R2", template_id="ecg-12lead@1.0.0",
                 answers={"rate_bpm": 80, "rhythm": "sinus", "pr_ms": 160,
                          "qrs_ms": 90, "qtc_ms": 480, "findings": "x",
                          "interpretation": "y", "acuity": "routine"},
                 case_id="C-R2", author_id="rad-1", day=0)
        with self.assertRaises(SerialError):
            compare_ecg(a, rk.reports["R2"])


class TestFetalBiometry(unittest.TestCase):
    def test_in_envelope(self):
        out = fetal_biometry(26, {"bpd": 65, "hc": 230, "ac": 220,
                                  "fl": 48})
        self.assertEqual(out["flags"], [])
        self.assertEqual(out["ref_version"], BIOMETRY_REF_VERSION)
        self.assertTrue(all(v["in_envelope"]
                            for v in out["assessed"].values()))

    def test_small_for_dates_flagged(self):
        out = fetal_biometry(26, {"ac": 150})
        self.assertTrue(any("below p5" in f for f in out["flags"]))

    def test_outside_table_abstains_honestly(self):
        out = fetal_biometry(12, {"bpd": 20})
        self.assertIn("abstained", out)

    def test_implausible_ga_and_empty_refused(self):
        with self.assertRaises(SerialError):
            fetal_biometry(50, {"bpd": 90})
        with self.assertRaises(SerialError):
            fetal_biometry(26, {})
        with self.assertRaises(SerialError):
            fetal_biometry(26, {"femur_cubits": 40})


if __name__ == "__main__":
    unittest.main()
