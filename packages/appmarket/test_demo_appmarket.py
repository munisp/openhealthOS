"""App-marketplace demo seed tests."""

import json
import os
import tempfile
import unittest

import demo_appmarket as demo


class TestAppmarketDemo(unittest.TestCase):
    def test_deterministic(self):
        a = demo.appmarket_payload(demo.seed_appmarket())
        b = demo.appmarket_payload(demo.seed_appmarket())
        self.assertEqual(a, b)

    def test_revocation_visible_with_dead_token(self):
        p = demo.appmarket_payload(demo.seed_appmarket())
        revoked = [i for i in p["installs"] if i["state"] == "revoked"]
        self.assertEqual(len(revoked), 1)
        self.assertTrue(revoked[0]["revoke_reason"])
        dead = [t for t in p["tokens"] if t["state"] == "revoked"]
        self.assertEqual(len(dead), 1)
        self.assertEqual(dead[0]["app_id"], revoked[0]["app_id"])

    def test_consent_ref_on_patient_launch(self):
        p = demo.appmarket_payload(demo.seed_appmarket())
        patient_tok = next(t for t in p["tokens"]
                           if t["app_id"] == "patient-viewer-sim")
        self.assertTrue(patient_tok["consent_ref"])

    def test_export_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = demo.export_appmarket(demo.seed_appmarket(),
                                         os.path.join(td, "m.json"))
            back = json.load(open(path))
        self.assertEqual(back["version"], demo.DEMO_VERSION)


if __name__ == "__main__":
    unittest.main()
