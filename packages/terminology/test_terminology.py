"""test_terminology.py — unbound is not a code; licence-gated systems
ship empty; coverage shows its denominator."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "reportkit"))

from terminology import (TerminologyError, TerminologyMap,  # noqa: E402
                         TERMINOLOGY_VERSION)
from reportkit import ReportKit  # noqa: E402


class TestMapLoading(unittest.TestCase):
    def test_seed_binds_loinc_and_icd(self):
        m = TerminologyMap("obs-v1", deployment="nigeria-pilot")
        c = m.bind_field("observation@1.0.0", "kind", "spo2")
        self.assertEqual(c["system"], "LOINC")
        self.assertEqual(c["code"], "59408-5")
        c2 = m.bind_field("diagnosis@1.0.0", "code",
                          "essential-hypertension")
        self.assertEqual(c2["code"], "I10")

    def test_unbound_refuses(self):
        m = TerminologyMap("obs-v1", deployment="nigeria-pilot")
        with self.assertRaises(TerminologyError):
            m.bind_field("observation@1.0.0", "kind", "cvp")

    def test_snomed_requires_attestation_before_entries(self):
        m = TerminologyMap("m1", deployment="d1")
        with self.assertRaises(TerminologyError):
            TerminologyMap(
                "m2", deployment="d1",
                extra_entries={("observation@1.0.0", "kind", "cvp"):
                               ("SNOMED-CT", "123456", "CVP")})
        m.attest("SNOMED-CT", by="d1-compliance")
        m2 = TerminologyMap(
            "m3", deployment="d1", snomed_license_attested=True,
            extra_entries={("observation@1.0.0", "kind", "cvp"):
                           ("SNOMED-CT", "123456", "CVP")})
        c = m2.bind_field("observation@1.0.0", "kind", "cvp")
        self.assertEqual(c["system"], "SNOMED-CT")
        self.assertTrue(m2.attestations)

    def test_map_needs_deployment(self):
        with self.assertRaises(TerminologyError):
            TerminologyMap("m", deployment="")


class TestReportBinding(unittest.TestCase):
    def test_bind_report_walks_coded_fields(self):
        m = TerminologyMap("obs-v1", deployment="d1")
        template = {"id": "observation@1.0.0",
                    "sections": [
                        {"type": "code", "key": "kind",
                         "options": {"spo2": "SpO2", "cvp": "CVP"}},
                        {"type": "text", "key": "note"}]}
        out = m.bind_report(template, {"kind": "spo2",
                                       "note": "on room air"})
        self.assertEqual(out["codings"][0]["code"], "59408-5")
        self.assertEqual(out["map_version"], TERMINOLOGY_VERSION)

    def test_unbound_coded_answer_refuses_export(self):
        m = TerminologyMap("obs-v1", deployment="d1")
        template = {"id": "observation@1.0.0",
                    "sections": [
                        {"type": "code", "key": "kind",
                         "options": {"spo2": "SpO2", "cvp": "CVP"}}]}
        with self.assertRaises(TerminologyError):
            m.bind_report(template, {"kind": "cvp"})

    def test_answer_outside_coded_set_refuses(self):
        m = TerminologyMap("obs-v1", deployment="d1")
        template = {"id": "observation@1.0.0",
                    "sections": [
                        {"type": "code", "key": "kind",
                         "options": {"spo2": "SpO2"}}]}
        with self.assertRaises(TerminologyError):
            m.bind_report(template, {"kind": "sp02"})  # zero, not O

    def test_absent_optional_field_is_omitted_not_guessed(self):
        m = TerminologyMap("obs-v1", deployment="d1")
        template = {"id": "observation@1.0.0",
                    "sections": [
                        {"type": "code", "key": "kind",
                         "options": {"spo2": "SpO2"}},
                        {"type": "code", "key": "scale",
                         "options": {"scale-1": "1", "scale-2": "2"}}]}
        out = m.bind_report(template, {"kind": "spo2"})
        self.assertEqual(len(out["codings"]), 1)

    def test_coverage_denominator(self):
        m = TerminologyMap("obs-v1", deployment="d1")
        template = {"id": "observation@1.0.0",
                    "sections": [
                        {"type": "code", "key": "kind",
                         "options": {"spo2": "SpO2", "cvp": "CVP"}}]}
        cov = m.coverage(template)
        self.assertEqual(cov["coded_values"], 2)
        self.assertEqual(cov["bound"], 1)
        self.assertEqual(cov["coverage_rate"], 0.5)

    def test_binds_real_reportkit_template(self):
        # the birads template ships coded fields; with no birads
        # bindings in the seed, export refuses — the map is the
        # worklist, and refusal is the point
        rk = ReportKit(signing_key=b"k")
        m = TerminologyMap("obs-v1", deployment="d1")
        t = rk.template("birads-mammo@1.0.0")
        cov = m.coverage(t)
        self.assertEqual(cov["bound"], 0)
        self.assertGreater(cov["coded_values"], 0)
        with self.assertRaises(TerminologyError):
            m.bind_report(t, {"breast_density": "a",
                              "birads_category": "1"})


if __name__ == "__main__":
    unittest.main()
