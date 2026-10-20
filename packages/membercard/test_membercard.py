#!/usr/bin/env python3
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from keyring_shared import TrustError, RevokedError
from membercard import mint, verify, main

RING = {"k2026": "membercard-secret-0001"}


def env():
    return patch.dict(os.environ, {"OH_QR_KEYS": json.dumps(RING), "OH_QR_ACTIVE": "k2026"}, clear=True)


class TestMemberCard(unittest.TestCase):
    def test_roundtrip(self):
        with env():
            tag = mint("NG-0001", "BHCPF-VUL", "2027-03-31")
            out = verify(tag, now=int(time.time()))
        self.assertTrue(tag.startswith("OHMEM1.k2026.NG-0001.BHCPF-VUL.20270331."))
        self.assertEqual(out["member"], "NG-0001")
        self.assertEqual(out["scheme"], "BHCPF-VUL")
        self.assertFalse(out["expired"])

    def test_expired_informational_not_rejected(self):
        with env():
            tag = mint("NG-0001", "BHCPF-VUL", "2026-01-01")
            out = verify(tag, now=int(time.mktime((2026, 6, 1, 0, 0, 0, 0, 0, 0))))
        self.assertTrue(out["expired"])

    def test_tampered_scheme_rejected(self):
        with env():
            tag = mint("NG-0001", "BHCPF-VUL", "2027-03-31")
            with self.assertRaises(TrustError):
                verify(tag.replace("BHCPF-VUL", "PRIVATE-HMO"), now=int(time.time()))

    def test_revoked_card_rejected(self):
        with env():
            tag = mint("NG-0002", "BHCPF-VUL", "2027-03-31")
            body = ".".join(tag.split(".")[:-1])
            with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
                fh.write(json.dumps({"body": body}) + "\n")
                path = fh.name
            try:
                with self.assertRaises(RevokedError):
                    verify(tag, revoked_path=path)
            finally:
                os.unlink(path)

    def test_no_key_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(TrustError):
                mint("NG-0001", "BHCPF-VUL", "2027-03-31")

    def test_bad_date_rejected(self):
        with env():
            with self.assertRaises(ValueError):
                mint("NG-0001", "BHCPF-VUL", "31-03-2027")

    def test_cli(self):
        with env():
            self.assertEqual(main(["mint", "NG-0001", "BHCPF-VUL", "--until", "2027-03-31"]), 0)


if __name__ == "__main__":
    unittest.main()
