"""Tests for packages/formbuilder — dynamic forms as registry entries."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formbuilder import FormSchema, FormRegistry, Error  # noqa: E402


def vital_signs_form():
    return FormSchema("vs-intake", "Vital Signs Intake", fields=[
        {"name": "patient_weight", "type": "number", "label": "Weight kg",
         "min": 0.5, "max": 400, "required": True},
        {"name": "visit_type", "type": "choice", "label": "Visit type",
         "options": ["new", "follow-up", "emergency"], "required": True},
        {"name": "notes", "type": "text", "label": "Notes"},
        {"name": "consent_given", "type": "boolean", "label": "Consent",
         "required": True},
    ])


class TestSchema(unittest.TestCase):
    def test_valid_schema(self):
        s = vital_signs_form()
        self.assertEqual(len(s.fields), 4)
        self.assertEqual(len(s.fingerprint()), 64)

    def test_duplicate_field_refused(self):
        with self.assertRaises(Error):
            FormSchema("bad", "Bad", fields=[
                {"name": "x", "type": "text", "label": "X"},
                {"name": "x", "type": "text", "label": "X2"}])

    def test_unknown_type_refused(self):
        with self.assertRaises(Error):
            FormSchema("bad", "Bad", fields=[
                {"name": "x", "type": "matrix", "label": "X"}])

    def test_choice_needs_options(self):
        with self.assertRaises(Error):
            FormSchema("bad", "Bad", fields=[
                {"name": "x", "type": "choice", "label": "X"}])


class TestRatification(unittest.TestCase):
    def test_four_eyes(self):
        s = vital_signs_form()
        s.propose(by="nurse-1")
        with self.assertRaises(Error):
            s.ratify(by="nurse-1")
        s.ratify(by="matron-1")
        self.assertEqual(s.state, "live")

    def test_draft_form_refuses_submission(self):
        s = vital_signs_form()
        with self.assertRaises(Error):
            s.validate_submission({})


class TestValidation(unittest.TestCase):
    def setUp(self):
        self.s = vital_signs_form()
        self.s.propose(by="nurse-1")
        self.s.ratify(by="matron-1")

    def test_valid_submission(self):
        errors = self.s.validate_submission({
            "patient_weight": 72.5, "visit_type": "new",
            "consent_given": True, "notes": "walk-in"})
        self.assertEqual(errors, [])

    def test_required_absent(self):
        errors = self.s.validate_submission({"visit_type": "new",
                                             "consent_given": True})
        self.assertTrue(any("required" in e for e in errors))

    def test_extra_keys_refused(self):
        with self.assertRaises(Error):
            # registry-level submit refuses; schema returns errors
            reg = FormRegistry("F1")
            reg.register(self.s)
            reg.submit("vs-intake", "P1",
                       {"patient_weight": 70, "visit_type": "new",
                        "consent_given": True, "hacker_field": "x"},
                       by="nurse-1", day=1)

    def test_type_violations(self):
        errors = self.s.validate_submission({
            "patient_weight": "heavy", "visit_type": "new",
            "consent_given": True})
        self.assertTrue(any("expected number" in e for e in errors))

    def test_choice_validation(self):
        errors = self.s.validate_submission({
            "patient_weight": 70, "visit_type": "urgent",
            "consent_given": True})
        self.assertTrue(any("not in options" in e for e in errors))

    def test_bounds(self):
        errors = self.s.validate_submission({
            "patient_weight": 500, "visit_type": "new",
            "consent_given": True})
        self.assertTrue(any("above max" in e for e in errors))


class TestRegistry(unittest.TestCase):
    def test_submit_and_retrieve(self):
        s = vital_signs_form()
        s.propose(by="nurse-1")
        s.ratify(by="matron-1")
        reg = FormRegistry("FAC-1")
        reg.register(s)
        sub = reg.submit("vs-intake", "Pax-1",
                         {"patient_weight": 70, "visit_type": "follow-up",
                          "consent_given": True},
                         by="nurse-1", day=1)
        self.assertEqual(len(sub["sha256"]), 64)
        self.assertEqual(sub["schema_sha256"], s.fingerprint())
        self.assertEqual(len(reg.submissions_for("Pax-1")), 1)

    def test_retired_form_refuses(self):
        s = vital_signs_form()
        s.propose(by="nurse-1")
        s.ratify(by="matron-1")
        s.retire(by="matron-1", reason="replaced by v2")
        with self.assertRaises(Error):
            s.validate_submission({})


if __name__ == "__main__":
    unittest.main()
