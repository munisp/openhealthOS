import unittest

from fhirguard import (AI_TAG, FHIRGuardError, PROVENANCE_SYSTEM,
                       validate_ai_write)

KNOWN = {"patient-abc123", "patient-def456"}


def good_condition():
    return {
        "resourceType": "Condition",
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                                       "code": "active"}]},
        "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10-cm",
                             "code": "E11.9"}]},
        "subject": {"reference": "Patient/patient-abc123"},
        "recordedDate": "2026-10-20",
    }


class TestValidateAIWrite(unittest.TestCase):
    def test_good_write_passes_and_gets_tagged(self):
        out = validate_ai_write(good_condition(), KNOWN, "triage-llm")
        tags = out["meta"]["tag"]
        self.assertTrue(any(t["system"] == PROVENANCE_SYSTEM and t["code"] == AI_TAG
                            for t in tags))
        self.assertIn("triage-llm", tags[0]["display"])

    def test_input_not_mutated(self):
        r = good_condition()
        validate_ai_write(r, KNOWN, "triage-llm")
        self.assertNotIn("meta", r)

    def test_unknown_patient_rejected(self):
        r = good_condition()
        r["subject"] = {"reference": "Patient/ghost-999"}
        with self.assertRaisesRegex(FHIRGuardError, "not in the SHR"):
            validate_ai_write(r, KNOWN, "triage-llm")

    def test_unknown_code_system_rejected(self):
        r = good_condition()
        r["code"]["coding"][0]["system"] = "http://example.org/made-up-codes"
        with self.assertRaisesRegex(FHIRGuardError, "not recognised"):
            validate_ai_write(r, KNOWN, "triage-llm")

    def test_prose_status_rejected(self):
        r = good_condition()
        r["clinicalStatus"]["coding"][0]["code"] = "probably active"
        with self.assertRaisesRegex(FHIRGuardError, "status"):
            validate_ai_write(r, KNOWN, "triage-llm")

    def test_fuzzy_date_rejected(self):
        r = good_condition()
        r["recordedDate"] = "last Tuesday"
        with self.assertRaisesRegex(FHIRGuardError, "ISO-8601"):
            validate_ai_write(r, KNOWN, "triage-llm")

    def test_entered_in_error_rejected(self):
        r = good_condition()
        r["clinicalStatus"]["coding"][0]["code"] = "entered-in-error"
        with self.assertRaisesRegex(FHIRGuardError, "entered-in-error"):
            validate_ai_write(r, KNOWN, "triage-llm")

    def test_novel_resource_type_rejected(self):
        r = {"resourceType": "CarePlan", "subject": {"reference": "Patient/patient-abc123"}}
        with self.assertRaisesRegex(FHIRGuardError, "allowlist"):
            validate_ai_write(r, KNOWN, "triage-llm")

    def test_all_problems_reported_at_once(self):
        r = good_condition()
        r["subject"] = {"reference": "Patient/ghost"}
        r["recordedDate"] = "someday"
        r["code"]["coding"][0]["system"] = "http://bogus"
        with self.assertRaises(FHIRGuardError) as ctx:
            validate_ai_write(r, KNOWN, "triage-llm")
        msg = str(ctx.exception)
        self.assertIn("not in the SHR", msg)
        self.assertIn("ISO-8601", msg)
        self.assertIn("not recognised", msg)

    def test_observation_loinc_passes(self):
        r = {"resourceType": "Observation", "status": "final",
             "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}]},
             "subject": {"reference": "Patient/patient-def456"},
             "effectiveDateTime": "2026-10-20T09:30:00Z"}
        out = validate_ai_write(r, KNOWN, "triage-llm")
        self.assertEqual(out["status"], "final")

    def test_idempotent_tag(self):
        r = good_condition()
        once = validate_ai_write(r, KNOWN, "triage-llm")
        twice = validate_ai_write(once, KNOWN, "triage-llm")
        tags = [t for t in twice["meta"]["tag"] if t["code"] == AI_TAG]
        self.assertEqual(len(tags), 1)


if __name__ == "__main__":
    unittest.main()
