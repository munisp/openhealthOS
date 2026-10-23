#!/usr/bin/env python3
import json
import os
import time
import unittest
from unittest.mock import patch

from qr_keyring import TrustError
from presenceqr import mint, scan, main

RING = {"k2026": "presence-secret-0000001"}


def env():
    return patch.dict(os.environ, {"OH_QR_KEYS": json.dumps(RING), "OH_QR_ACTIVE": "k2026"}, clear=True)


# 2026-10-20 12:00 UTC
NOW = 1792497600


class TestPresenceQR(unittest.TestCase):
    def test_mint_scan_roundtrip(self):
        with env():
            tag = mint("KDH-01", "2026-10-20")
            ev = scan(tag, "NG-0001", now=NOW)
        self.assertTrue(tag.startswith("OHPRS1.k2026.KDH-01.20261020."))
        self.assertEqual(ev["presence"], "qr-day")
        self.assertEqual(ev["facility_id"], "KDH-01")
        self.assertEqual(ev["patient_id"], "NG-0001")
        self.assertEqual(ev["date"], "2026-10-20")

    def test_yesterday_code_is_replay(self):
        with env():
            tag = mint("KDH-01", "2026-10-19")
            with self.assertRaises(TrustError):
                scan(tag, "NG-0001", now=NOW)

    def test_tomorrow_code_is_replay(self):
        with env():
            tag = mint("KDH-01", "2026-10-21")
            with self.assertRaises(TrustError):
                scan(tag, "NG-0001", now=NOW)

    def test_tampered_facility_rejected(self):
        with env():
            tag = mint("KDH-01", "2026-10-20")
            with self.assertRaises(TrustError):
                scan(tag.replace("KDH-01", "KDH-99"), "NG-0001", now=NOW)

    def test_patient_required(self):
        with env():
            tag = mint("KDH-01", "2026-10-20")
            with self.assertRaises(TrustError):
                scan(tag, "", now=NOW)

    def test_cli_mint(self):
        with env():
            self.assertEqual(main(["mint", "KDH-01", "--date", "2026-10-20"]), 0)


if __name__ == "__main__":
    unittest.main()
