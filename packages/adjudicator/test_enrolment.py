#!/usr/bin/env python3
"""Enrolment store + enriched eligibility (ADR-0017)."""
import json
import os
import tempfile
import unittest

import adjudicate
from enrolment import (EnrolmentError, enrol, load_members, suspend,
                       validate)

REC = {"member_id": "NG-T001", "name": "Test Patient", "scheme": "BHCPF-VUL",
       "valid_until": "2027-12-31", "copay_waiver": True}


class EnrolmentTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "members.json")
        self._old = os.environ.get("MEMBERS_FILE")
        os.environ["MEMBERS_FILE"] = self.path
        adjudicate._members_cache = None

    def tearDown(self):
        self.dir.cleanup()
        if self._old is None:
            os.environ.pop("MEMBERS_FILE", None)
        else:
            os.environ["MEMBERS_FILE"] = self._old
        adjudicate._members_cache = None

    def test_enrol_roundtrip_and_update_never_duplicates(self):
        enrol(REC, self.path)
        members = load_members(self.path)
        self.assertEqual(members["NG-T001"]["scheme"], "BHCPF-VUL")
        self.assertTrue(members["NG-T001"]["active"])
        # Renewal updates the same identity.
        enrol({**REC, "valid_until": "2028-12-31"}, self.path)
        members = load_members(self.path)
        self.assertEqual(len(members), 1)
        self.assertEqual(members["NG-T001"]["valid_until"], "2028-12-31")

    def test_unknown_scheme_refused_at_enrolment(self):
        with self.assertRaises(EnrolmentError):
            validate({**REC, "scheme": "ACME-GOLD"})

    def test_suspend_needs_reason_and_keeps_history(self):
        enrol(REC, self.path)
        with self.assertRaises(EnrolmentError):
            suspend("NG-T001", "", self.path)
        out = suspend("NG-T001", "duplicate enrolment under second name", self.path)
        self.assertFalse(out["active"])
        self.assertIn("NG-T001", load_members(self.path))  # never deleted

    def test_eligibility_reads_the_store_and_says_why(self):
        enrol(REC, self.path)
        ok = adjudicate.check_eligibility("NG-T001", "2026-10-18")
        self.assertTrue(ok["eligible"])
        self.assertEqual(ok["benefit_services"], 7)
        enrol({**REC, "member_id": "NG-T002", "scheme": "STATE-BASIC",
               "valid_until": "2026-01-01", "biometric_required": True}, self.path)
        expired = adjudicate.check_eligibility("NG-T002", "2026-10-18")
        self.assertFalse(expired["eligible"])
        self.assertIn("expired", expired["reason"])
        self.assertTrue(expired["biometric_required"])
        suspend("NG-T001", "fraud investigation", self.path)
        suspended = adjudicate.check_eligibility("NG-T001", "2026-10-18")
        self.assertFalse(suspended["eligible"])
        self.assertIn("fraud investigation", suspended["reason"])
        unknown = adjudicate.check_eligibility("NG-NOPE", "2026-10-18")
        self.assertIn("enrol", unknown["reason"])

    def test_store_change_visible_without_reimport(self):
        enrol(REC, self.path)
        self.assertTrue(adjudicate.check_eligibility("NG-T001", "2026-10-18")["eligible"])
        # External writer (state sync) replaces the file — cache invalidates.
        enrol({**REC, "member_id": "NG-T003"}, self.path)
        self.assertTrue(adjudicate.check_eligibility("NG-T003", "2026-10-18")["eligible"])

    def test_fixture_fallback_when_no_file(self):
        os.environ.pop("MEMBERS_FILE")
        adjudicate._members_cache = None
        ok = adjudicate.check_eligibility("NG-0001", "2026-10-18")
        self.assertTrue(ok["eligible"])

    def test_atomic_write_leaves_no_torn_file(self):
        enrol(REC, self.path)
        with open(self.path) as f:
            json.load(f)  # parses cleanly
        leftovers = [n for n in os.listdir(self.dir.name) if n.startswith(".members-")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
