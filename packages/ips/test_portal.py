"""Portal payload tests — the patient reads the same record as the
receiving clinician, withheld is stated, the checksum is the contract."""

import json
import os
import tempfile
import unittest

from ips import build_ips
from portal import (PORTAL_VERSION, PortalError, export_portal,
                    portal_payload)


def _bundle():
    return build_ips(
        {"id": "Pax-0001", "name": "Ada O (Sim)", "birthDate": "1990-01-01",
         "gender": "female", "country": "ng"},
        sections={
            "medications": {"entries": [
                {"display": "artemether-lumefantrine 80/480 bd 3d"}]},
            "allergies": {"empty_reason": "none-known"},
            "problems": {"entries": [{"display": "malaria, uncomplicated"}]},
            "immunizations": {"entries": [{"display": "COVID-19 x2"}]},
            "pregnancy": {"empty_reason": "withheld-by-consent"},
        },
        author="OpenHealth OS", generated_day=1)


class TestPortalPayload(unittest.TestCase):
    def test_rejects_non_bundle(self):
        with self.assertRaises(PortalError):
            portal_payload({"resourceType": "Patient"})

    def test_sections_render_plain_language(self):
        p = portal_payload(_bundle())
        titles = {s["title"] for s in p["sections"]}
        self.assertIn("Your medications", titles)
        self.assertIn("Your allergies", titles)
        meds = next(s for s in p["sections"] if s["key"] == "medications")
        self.assertEqual(meds["state"], "recorded")
        self.assertIn("artemether", meds["items"][0])

    def test_empty_and_withheld_stated(self):
        p = portal_payload(_bundle())
        allergies = next(s for s in p["sections"] if s["key"] == "allergies")
        self.assertEqual(allergies["display_state"], "None known")
        preg = next(s for s in p["sections"] if s["key"] == "pregnancy")
        self.assertEqual(preg["state"], "withheld-by-consent")
        self.assertIn("ask your clinician", preg["display_state"])

    def test_fingerprints_carried(self):
        p = portal_payload(_bundle())
        self.assertTrue(p["ips_fingerprint"].startswith("sha256:"))
        self.assertEqual(len(p["sha256"]), 64)

    def test_patient_identity_from_bundle(self):
        p = portal_payload(_bundle())
        self.assertEqual(p["patient"]["name"], "Ada O (Sim)")
        self.assertEqual(p["version"], PORTAL_VERSION)

    def test_visits_and_share_link(self):
        p = portal_payload(_bundle(),
                           visits=[{"day": 3, "facility": "PHC Sim",
                                    "summary": "malaria treated",
                                    "note_sha256": "ab" * 32}],
                           share_link="shlink:/example")
        self.assertEqual(p["visits"][0]["facility"], "PHC Sim")
        self.assertEqual(p["share_link"], "shlink:/example")

    def test_export_writes_parseable(self):
        p = portal_payload(_bundle())
        with tempfile.TemporaryDirectory() as td:
            path = export_portal(p, os.path.join(td, "record.json"))
            with open(path) as fh:
                back = json.load(fh)
        self.assertEqual(back["sha256"], p["sha256"])


if __name__ == "__main__":
    unittest.main()
