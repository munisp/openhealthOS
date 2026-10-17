"""Tests for the structural FHIR validator, over the repo's own profiles
and example resources — the profiles and the validator are pinned together
so a profile change that breaks examples fails here, not in a pilot."""

import json
import unittest
from pathlib import Path

from validate import load_requirements, validate

ROOT = Path(__file__).resolve().parent.parent.parent / "fhir"


def load(pair):
    profile = json.load(open(ROOT / "profiles" / f"openhealth-{pair}.json"))
    resource = json.load(open(ROOT / "examples" / f"{pair}-valid.json"))
    return resource, profile


class TestRequirements(unittest.TestCase):
    def test_required_elements_extracted(self):
        _, reqs = load_requirements(json.load(open(ROOT / "profiles" / "openhealth-claim.json")))
        ids = [r.element_id for r in reqs]
        self.assertIn("Claim.status", ids)
        self.assertIn("Claim.extension", ids)  # sliced: adjudicationStatus
        slices = {r.slice_name for r in reqs if r.slice_name}
        self.assertIn("adjudicationStatus", slices)


class TestValidExamples(unittest.TestCase):
    def test_patient_valid(self):
        r, p = load("patient")
        self.assertEqual(validate(r, p), [])

    def test_encounter_valid(self):
        r, p = load("encounter")
        self.assertEqual(validate(r, p), [])

    def test_claim_valid(self):
        r, p = load("claim")
        self.assertEqual(validate(r, p), [])

    def test_consent_valid(self):
        r, p = load("consent")
        self.assertEqual(validate(r, p), [])


class TestViolationsDetected(unittest.TestCase):
    def test_patient_without_identifier(self):
        _, profile = load("patient")
        bad = json.load(open(ROOT / "examples" / "patient-invalid.json"))
        violations = validate(bad, profile)
        self.assertTrue(any("identifier" in v for v in violations))

    def test_claim_missing_insurance_and_adjudication(self):
        _, profile = load("claim")
        bad = json.load(open(ROOT / "examples" / "claim-invalid.json"))
        violations = validate(bad, profile)
        self.assertTrue(any("insurance" in v for v in violations))
        self.assertTrue(any("adjudicationStatus" in v for v in violations))

    def test_wrong_resource_type(self):
        _, profile = load("patient")
        violations = validate({"resourceType": "Observation"}, profile)
        self.assertTrue(any("resourceType" in v for v in violations))

    def test_every_example_resource_type_matches_its_profile(self):
        # A misnamed example would silently validate nothing.
        for pair in ["patient", "encounter", "claim", "consent"]:
            r, p = load(pair)
            rtype, _ = load_requirements(p)
            self.assertEqual(r["resourceType"], rtype)


if __name__ == "__main__":
    unittest.main()
