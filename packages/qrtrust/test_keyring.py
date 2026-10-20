#!/usr/bin/env python3
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from keyring import (BUDGET_MAX_BYTES, RevokedError, TrustError, check_budget,
                     load_keyring, load_revocations, sign, verify_with_ring)

RING = {"k2025": "old-secret-0000000001", "k2026": "new-secret-0000000002"}


class TestKeyring(unittest.TestCase):
    def test_json_ring(self):
        with patch.dict(os.environ, {"OH_QR_KEYS": json.dumps(RING), "OH_QR_ACTIVE": "k2026"}):
            ring, active = load_keyring()
        self.assertEqual(active, "k2026")
        self.assertEqual(len(ring), 2)

    def test_legacy_single_secret_degrades_to_k0(self):
        with patch.dict(os.environ, {"OH_QR_KEYS": "single-secret-value-1234"}, clear=False):
            os.environ.pop("OH_QR_ACTIVE", None)
            ring, active = load_keyring()
        self.assertEqual(ring, {"k0": b"single-secret-value-1234"})
        self.assertEqual(active, "k0")

    def test_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TrustError):
                load_keyring()
        with patch.dict(os.environ, {"OH_QR_KEYS": json.dumps(RING), "OH_QR_ACTIVE": "knope"}):
            with self.assertRaises(TrustError):
                load_keyring()

    def test_short_keys_ignored(self):
        with patch.dict(os.environ, {"OH_QR_KEYS": json.dumps({"k1": "short"})}):
            with self.assertRaises(TrustError):
                load_keyring()


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.ring = {k: v.encode() for k, v in RING.items()}
        body = "OHAST2.k2025.KDH-01.MRI-03.1761000000"
        self.tag = f"{body}.{sign(self.ring['k2025'], body)}"

    def test_old_key_still_verifies_after_rotation(self):
        fields = verify_with_ring(self.tag, self.ring, "OHAST2", 3)
        self.assertEqual(fields, ["KDH-01", "MRI-03", "1761000000"])

    def test_unknown_kid_rejected(self):
        bad = self.tag.replace("k2025", "k9999")
        with self.assertRaises(TrustError):
            verify_with_ring(bad, self.ring, "OHAST2", 3)

    def test_tamper_rejected(self):
        bad = self.tag.replace("MRI-03", "MRI-04")
        with self.assertRaises(TrustError):
            verify_with_ring(bad, self.ring, "OHAST2", 3)

    def test_revocation_hard_reject(self):
        body = ".".join(self.tag.split(".")[:-1])
        with self.assertRaises(RevokedError):
            verify_with_ring(self.tag, self.ring, "OHAST2", 3, revoked={body})

    def test_revocation_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
            fh.write(json.dumps({"body": "OHAST2.k2025.X.Y.1"}) + "\n")
            fh.write("garbage\n")
            path = fh.name
        try:
            revoked = load_revocations(path)
            self.assertEqual(revoked, {"OHAST2.k2025.X.Y.1"})
            self.assertEqual(load_revocations(path + ".missing"), set())
        finally:
            os.unlink(path)


class TestBudget(unittest.TestCase):
    def test_small_payload_ok(self):
        r = check_budget("OHAST2.k2026.KDH-01.MRI-03.1761000000.ab12cd34ef56ab12")
        self.assertTrue(r["within_target"])
        self.assertEqual(r["ec_level"], "Q")

    def test_ceiling_refused(self):
        with self.assertRaises(TrustError):
            check_budget("x" * (BUDGET_MAX_BYTES + 1))

    def test_big_but_scannable_warns_only(self):
        r = check_budget("x" * 1200)
        self.assertFalse(r["within_target"])  # advisory, not an error


if __name__ == "__main__":
    unittest.main()
