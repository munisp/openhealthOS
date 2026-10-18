#!/usr/bin/env python3
"""Linkage gate tests (ADR-0018): the ghost-claim shapes — no encounter,
forged reference, mismatched evidence — all PEND with reasons; a clean
biometric-presence claim APPROVES; presence strength is priced, not
blocked."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adjudicate as adj
from linkage import check_linkage, load_encounters

MEMBER = "NG-0001"  # fixture: Amina Yusuf, BHCPF-VUL, active
BASE_CLAIM = {
    "claim_id": "CLM-T1", "member_id": MEMBER, "provider_id": "NG-KD-0014",
    "service_date": "2026-10-12", "items": [{"code": "MAL-RDT", "qty": 1}],
    "claimed_ngn": 0,
}
ENCOUNTERS = [
    {"encounter_id": "ENC-A", "patient_id": MEMBER, "facility_id": "NG-KD-0014",
     "date": "2026-10-12", "presence": "biometric"},
    {"encounter_id": "ENC-B", "patient_id": MEMBER, "facility_id": "NG-KD-0014",
     "date": "2026-10-12", "presence": "self"},
    {"encounter_id": "ENC-C", "patient_id": "NG-0002", "facility_id": "NG-KD-0002",
     "date": "2026-10-13", "presence": "card"},
]


class TestCheckLinkage(unittest.TestCase):
    def test_no_encounter_reference_pends(self):
        r = check_linkage(dict(BASE_CLAIM), ENCOUNTERS)
        self.assertFalse(r["linked"])
        self.assertTrue(any("no encounter reference" in f for f in r["flags"]))

    def test_forged_reference_named(self):
        c = dict(BASE_CLAIM, encounter_id="ENC-999")
        r = check_linkage(c, ENCOUNTERS)
        self.assertFalse(r["linked"])
        self.assertTrue(any("not on record" in f for f in r["flags"]))

    def test_mismatched_evidence(self):
        c = dict(BASE_CLAIM, encounter_id="ENC-C")  # other patient/facility/date
        r = check_linkage(c, ENCOUNTERS)
        self.assertFalse(r["linked"])
        self.assertIn("mismatch", r["flags"][0])
        self.assertIn("NG-0002", r["flags"][0])

    def test_clean_biometric_link(self):
        c = dict(BASE_CLAIM, encounter_id="ENC-A")
        r = check_linkage(c, ENCOUNTERS)
        self.assertTrue(r["linked"])
        self.assertEqual(r["presence"], "biometric")
        self.assertEqual(r["flags"], [])

    def test_self_presence_flags_for_sampling(self):
        c = dict(BASE_CLAIM, encounter_id="ENC-B")
        r = check_linkage(c, ENCOUNTERS)
        self.assertTrue(r["linked"])
        self.assertTrue(any(f.startswith("PRESENCE:") for f in r["flags"]))

    def test_malformed_lines_and_missing_file_are_honest_empty(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "enc.jsonl")
            with open(p, "w") as fh:
                fh.write('{"encounter_id":"E1","patient_id":"P","facility_id":"F","date":"2026-01-01"}\n')
                fh.write("not json at all\n")
                fh.write('{"encounter_id":"E2"}\n')  # incomplete
            got = load_encounters(p)
            self.assertEqual(len(got), 1)
            self.assertEqual(load_encounters(os.path.join(d, "nope.jsonl")), [])


class TestAdjudicateLinkage(unittest.TestCase):
    """The gate inside the full pipeline (fixture members/encounters)."""

    def _claim(self, **kw):
        c = dict(BASE_CLAIM)
        c.update(kw)
        return c

    def test_ghost_claim_pends_with_reason(self):
        r = adj.adjudicate(self._claim())  # no encounter_id
        self.assertEqual(r["decision"], "PEND")
        self.assertTrue(any("LINKAGE" in f for f in r["flags"]))
        self.assertFalse(r["linkage"]["linked"])
        # A pended verdict states why in reasons, not only flags.
        self.assertTrue(any("LINKAGE" in x for x in r["reasons"]))

    def test_forged_reference_pends(self):
        r = adj.adjudicate(self._claim(encounter_id="ENC-404"))
        self.assertEqual(r["decision"], "PEND")
        self.assertTrue(any("not on record" in f for f in r["flags"]))

    def test_biometric_encounter_approves(self):
        # ENC-0001 fixture: NG-0001 at NG-KD-0014 on 2026-10-12, biometric.
        r = adj.adjudicate(self._claim(encounter_id="ENC-0001"))
        self.assertEqual(r["decision"], "APPROVE")
        self.assertTrue(r["linkage"]["linked"])
        self.assertEqual(r["linkage"]["presence"], "biometric")

    def test_mismatched_member_pends(self):
        r = adj.adjudicate(self._claim(encounter_id="ENC-0002"))  # NG-0002's encounter
        self.assertEqual(r["decision"], "PEND")
        self.assertTrue(any("mismatch" in f for f in r["flags"]))

    def test_cli_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            enc = os.path.join(d, "enc.jsonl")
            with open(enc, "w") as fh:
                fh.write(json.dumps(ENCOUNTERS[0]) + "\n")
            env = dict(os.environ, ENCOUNTER_FILE=enc)
            proc = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "adjudicate.py")],
                input=json.dumps(self._claim(encounter_id="ENC-A")),
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            r = json.loads(proc.stdout)
            self.assertEqual(r["decision"], "APPROVE")
            self.assertEqual(r["linkage"]["presence"], "biometric")


if __name__ == "__main__":
    unittest.main()
