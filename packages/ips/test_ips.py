#!/usr/bin/env python3
"""IPS builder: valid document, required sections, dangling refs caught."""
import unittest

from ips import REQUIRED_SECTIONS, build_ips, ips_summary, validate_ips


class TestIPS(unittest.TestCase):
    def setUp(self):
        self.bundle = build_ips(
            {"id": "PT-1", "name": "Amina Bello", "birth_date": "1990-04-02", "gender": "female"},
            problems=[{"code": "J06.9", "display": "Acute upper respiratory infection"}],
            allergies=[{"code": "penicillin", "display": "Penicillin allergy"}],
            medications=[{"code": "ACT", "display": "Artemether/lumefantrine"}],
            immunizations=[{"code": "measles", "display": "Measles vaccination"}],
            observations=[{"code": "8480-6", "display": "Systolic BP", "value": "128 mmHg"}],
            facility="GH Kawo")

    def test_valid_document(self):
        self.assertEqual(validate_ips(self.bundle), [])
        self.assertEqual(self.bundle["resourceType"], "Bundle")
        self.assertEqual(self.bundle["type"], "document")

    def test_all_required_sections_present(self):
        comp = next(e["resource"] for e in self.bundle["entry"]
                    if e["resource"]["resourceType"] == "Composition")
        titles = {s["title"] for s in comp["section"]}
        for req in REQUIRED_SECTIONS:
            self.assertIn(req, titles)

    def test_dangling_reference_caught(self):
        comp = next(e["resource"] for e in self.bundle["entry"]
                    if e["resource"]["resourceType"] == "Composition")
        comp["subject"]["reference"] = "urn:uuid:ghost"
        errors = validate_ips(self.bundle)
        self.assertTrue(any("dangling reference" in e for e in errors))

    def test_missing_patient_caught(self):
        broken = {"resourceType": "Bundle", "type": "document",
                  "entry": [e for e in self.bundle["entry"]
                            if e["resource"]["resourceType"] != "Patient"]}
        self.assertIn("Patient entry required", validate_ips(broken))

    def test_summary_counts(self):
        counts = ips_summary(self.bundle)
        self.assertEqual(counts["Patient"], 1)
        self.assertEqual(counts["Composition"], 1)
        self.assertEqual(counts["Condition"], 1)
        self.assertEqual(counts["Observation"], 1)


if __name__ == "__main__":
    unittest.main()
