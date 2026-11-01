import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from reportkit import ReportKit, ReportKitError, TEMPLATES  # noqa: E402

BIRADS = {"findings": "no suspicious mass or calcification",
          "breast_density": "b", "birads_category": "1",
          "recommendation": "routine screening in 12 months"}


class TestTemplates(unittest.TestCase):
    def test_registry_lists_versioned_templates(self):
        ids = [t["id"] for t in ReportKit().templates()]
        self.assertIn("birads-mammo@1.0.0", ids)
        self.assertIn("cap-colon-resection@1.0.0", ids)

    def test_unknown_template_refused(self):
        with self.assertRaises(ReportKitError):
            ReportKit().template("free-text@9")

    def test_missing_required_refused(self):
        with self.assertRaises(ReportKitError) as ctx:
            ReportKit().validate("birads-mammo@1.0.0",
                                 {"findings": "clear"})
        self.assertIn("missing required", str(ctx.exception))

    def test_value_outside_coded_set_refused(self):
        with self.assertRaises(ReportKitError) as ctx:
            ReportKit().validate("birads-mammo@1.0.0",
                                 {**BIRADS, "birads_category": "7"})
        self.assertIn("not in coded set", str(ctx.exception))

    def test_unknown_field_refused_not_ignored(self):
        with self.assertRaises(ReportKitError) as ctx:
            ReportKit().validate("birads-mammo@1.0.0",
                                 {**BIRADS, "guess": "maybe cancer"})
        self.assertIn("not in template", str(ctx.exception))

    def test_implausible_number_refused(self):
        with self.assertRaises(ReportKitError):
            ReportKit().validate("ecg-12lead@1.0.0",
                                 {"rate_bpm": 900, "rhythm": "sinus",
                                  "pr_ms": 160, "qrs_ms": 90,
                                  "qtc_ms": 400, "findings": "normal",
                                  "interpretation": "normal",
                                  "acuity": "routine"})

    def test_optional_absent_field_omitted_not_guessed(self):
        clean = ReportKit().validate("birads-mammo@1.0.0", BIRADS)
        self.assertNotIn("comparison", clean)


class TestSignExport(unittest.TestCase):
    def setUp(self):
        self.rk = ReportKit(signing_key=b"deployment-key")
        self.rk.draft("R1", template_id="birads-mammo@1.0.0",
                      answers=BIRADS, case_id="case-1",
                      author_id="rad-1", day=1)

    def test_sign_and_verify_roundtrip(self):
        out = self.rk.sign("R1", signer_id="rad-1", day=2)
        self.assertEqual(out["status"], "signed")
        self.assertTrue(self.rk.verify("R1")["valid"])

    def test_tamper_breaks_verification(self):
        self.rk.sign("R1", signer_id="rad-1", day=2)
        self.rk.reports["R1"]["answers"]["birads_category"] = "5"
        self.assertFalse(self.rk.verify("R1")["valid"])

    def test_only_author_signs_and_only_once(self):
        with self.assertRaises(ReportKitError):
            self.rk.sign("R1", signer_id="rad-2", day=2)
        self.rk.sign("R1", signer_id="rad-1", day=2)
        with self.assertRaises(ReportKitError) as ctx:
            self.rk.sign("R1", signer_id="rad-1", day=3)
        self.assertIn("addenda", str(ctx.exception))

    def test_no_key_no_signature(self):
        rk = ReportKit()
        rk.draft("R2", template_id="birads-mammo@1.0.0", answers=BIRADS,
                 case_id="c", author_id="a", day=1)
        with self.assertRaises(ReportKitError) as ctx:
            rk.sign("R2", signer_id="a", day=1)
        self.assertIn("no signing key", str(ctx.exception))

    def test_fhir_export_shape(self):
        self.rk.sign("R1", signer_id="rad-1", day=2)
        fhir = self.rk.export_fhir("R1")
        self.assertEqual(fhir["resourceType"], "DiagnosticReport")
        self.assertEqual(fhir["status"], "final")
        self.assertEqual(fhir["_structured"]["template_id"],
                         "birads-mammo@1.0.0")
        self.assertTrue(fhir["extension"][0]["valueString"])

    def test_html_export_marks_unsigned_and_escapes(self):
        html_unsigned = self.rk.export_html("R1")
        self.assertIn("UNSIGNED DRAFT", html_unsigned)
        self.rk.sign("R1", signer_id="rad-1", day=2)
        html_signed = self.rk.export_html("R1")
        self.assertIn("content hash", html_signed)
        self.assertIn("Signed by rad-1", html_signed)


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.dir.name, "state.json")
        self.cli = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "cli.py")
        self.env = {**os.environ, "REPORTKIT_SIGNING_KEY": "k"}

    def tearDown(self):
        self.dir.cleanup()

    def call(self, *argv, env=None):
        return subprocess.run(
            [sys.executable, self.cli, self.db, *argv],
            capture_output=True, text=True, env=env or self.env)

    def test_draft_sign_verify_persists_across_processes(self):
        import json as j
        r = self.call("draft", j.dumps(
            {"report_id": "R1", "template_id": "birads-mammo@1.0.0",
             "answers": BIRADS, "case_id": "c1", "author_id": "a1",
             "day": 1}))
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.call("sign", j.dumps({"report_id": "R1",
                                       "signer_id": "a1", "day": 2}))
        self.assertEqual(r.returncode, 0, r.stdout)
        r = self.call("verify", j.dumps({"report_id": "R1"}))
        self.assertTrue(j.loads(r.stdout)["result"]["valid"])

    def test_domain_error_exits_2_with_message(self):
        import json as j
        r = self.call("draft", j.dumps(
            {"report_id": "R1", "template_id": "nope@1",
             "answers": {}, "case_id": "c", "author_id": "a", "day": 1}))
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown template", j.loads(r.stdout)["error"])

    def test_sign_without_key_fails_loud(self):
        import json as j
        env = {k: v for k, v in os.environ.items()
               if k != "REPORTKIT_SIGNING_KEY"}
        self.call("draft", j.dumps(
            {"report_id": "R9", "template_id": "birads-mammo@1.0.0",
             "answers": BIRADS, "case_id": "c", "author_id": "a",
             "day": 1}))
        r = self.call("sign", j.dumps({"report_id": "R9",
                                       "signer_id": "a", "day": 1}),
                      env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("no signing key", j.loads(r.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
