#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from casedef import CaseDefError, CaseDefRegistry  # noqa: E402

V1 = {"version": "ebola-v1",
      "suspect_criteria": {"fever": True, "travel_21d": True},
      "confirmed_criteria": {"lab_pcr_positive": True}}
V2 = {"version": "ebola-v2",
      "suspect_criteria": {"fever": True, "travel_21d": True,
                           "contact_with_case": True},
      "confirmed_criteria": {"lab_pcr_positive": True}}


class TestCaseDef(unittest.TestCase):
    def setUp(self):
        self.reg = CaseDefRegistry()
        self.reg.activate("ebola", V1, "2026-10-20", author="ncdc")

    def test_screen_under_current_version(self):
        out = self.reg.screen("ebola", "ebola-v1",
                              {"fever": True, "travel_21d": True},
                              "PHC-007", "2026-10-21")
        self.assertEqual(out["disposition"], "suspect")
        out2 = self.reg.screen("ebola", "ebola-v1",
                               {"fever": True, "travel_21d": False},
                               "PHC-007", "2026-10-21")
        self.assertEqual(out2["disposition"], "not-a-suspect")

    def test_stale_version_refuses_naming_current(self):
        self.reg.activate("ebola", V2, "2026-10-25", author="ncdc")
        with self.assertRaises(CaseDefError) as ctx:
            self.reg.screen("ebola", "ebola-v1",
                            {"fever": True}, "PHC-012", "2026-10-26")
        self.assertIn("ebola-v2", str(ctx.exception))

    def test_version_never_mutates(self):
        with self.assertRaises(CaseDefError):
            self.reg.activate("ebola", dict(V1), "2026-10-22", author="ncdc")

    def test_missing_criteria_refused_at_activation(self):
        with self.assertRaises(CaseDefError):
            self.reg.activate("ebola", {"version": "v9"}, "2026-10-22",
                              author="ncdc")

    def test_adoption_audit_names_stale_facilities(self):
        self.reg.screen("ebola", "ebola-v1",
                        {"fever": True, "travel_21d": True},
                        "PHC-007", "2026-10-21")
        self.reg.activate("ebola", V2, "2026-10-25", author="ncdc")
        audit = self.reg.adoption_audit("ebola")
        self.assertEqual(audit["current_version"], "ebola-v2")
        self.assertEqual(audit["screenings_by_version"], {"ebola-v1": 1})
        self.assertEqual(audit["stale_facilities"], ["PHC-007"])

    def test_unknown_definition_and_version_refused(self):
        with self.assertRaises(CaseDefError):
            self.reg.current("cholera")
        with self.assertRaises(CaseDefError):
            self.reg.screen("ebola", "ebola-v99", {}, "PHC-007",
                            "2026-10-21")


if __name__ == "__main__":
    unittest.main()
