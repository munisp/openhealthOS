"""Dedicated suite for telesupervision.py — signed bundle doctrine."""
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from telesupervision import CaseBundle

KEY = b"clinic-shared-secret"


class TestCaseBundle(unittest.TestCase):
    def test_signed_bundle_verifies(self):
        b = CaseBundle.create("chw-7", "pat-pseudo-1",
                              {"triage_path": "imci", "vitals": {}},
                              KEY, now=100)
        self.assertTrue(CaseBundle.verify(dict(b), KEY))

    def test_tampered_content_fails_verification(self):
        b = CaseBundle.create("chw-7", "pat-pseudo-1",
                              {"triage_path": "imci"}, KEY, now=100)
        b["content"]["triage_path"] = "edited-after-signing"
        self.assertFalse(CaseBundle.verify(b, KEY))

    def test_wrong_key_fails(self):
        b = CaseBundle.create("chw-7", "pat-pseudo-1", {}, KEY, now=1)
        self.assertFalse(CaseBundle.verify(b, b"other-key"))

    def test_missing_sig_fails(self):
        b = CaseBundle.create("chw-7", "pat-pseudo-1", {}, KEY, now=1)
        del b["sig"]
        self.assertFalse(CaseBundle.verify(b, KEY))


if __name__ == "__main__":
    unittest.main()
