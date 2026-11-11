"""Tests for idseamproof — the identity-seam demo surface (ADR-0079)."""
import hashlib
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "travelpass"))

import demo_idseam  # noqa: E402
import vhcover  # noqa: E402


class TestIdSeamProof(unittest.TestCase):
    def test_determinism(self):
        a = demo_idseam.run_demo()
        b = demo_idseam.run_demo()
        self.assertEqual(a, b)
        self.assertEqual(a["sha256"], b["sha256"])

    def test_stamp_contract(self):
        body = demo_idseam.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        body = demo_idseam.run_demo()
        self.assertEqual(body["generated_at"], demo_idseam.CLOCK)
        census = body["control"]["census"]
        self.assertEqual(census["sites_found"], 25)
        self.assertEqual(census["seamed_this_wave"], 23)
        self.assertEqual(len(census["refused"]), 1)
        self.assertIn("shl.py", census["refused"][0]["site"])
        outcomes = [d["outcome"] for d in body["drills"]]
        self.assertEqual(outcomes, ["stated", "stated", "refused",
                                    "stated", "refused", "stated"])
        cf = body["control"]["cover_flow"]
        self.assertTrue(cf["replayed_byte_identical"])
        self.assertEqual(cf["quote_id"], "Q-00000000")
        self.assertEqual(cf["ref"], "VHC-00000001")
        self.assertTrue(body["control"]["production_defaults"]
                        ["distinct_refs"])
        self.assertTrue(body["control"]["seam_sweep"]
                        ["replayed_identical"])
        self.assertTrue(body["control"]["surveillance_noise"]
                        ["replayed_identical"])

    def test_sweep_covers_every_seamed_site(self):
        body = demo_idseam.run_demo()
        sites = body["control"]["seam_sweep"]["sites"]
        self.assertEqual(len(sites), 10)
        self.assertEqual(sites["vhclaims.intake"], "CLM-00000000")
        self.assertEqual(sites["vhclaims.issue_gop"], "GOP-00000001")
        self.assertEqual(sites["vhregistry.register_incident"],
                         "INC-00000000")
        self.assertTrue(sites["travelpass.issue_pass"]
                        .startswith("tp_00000000"))

    def test_gate_is_real_without_the_demo(self):
        """The seams exist in the production classes, not the demo."""
        ring = vhcover.KeyRing()
        ring.register("k1", "secret-one", active_from=0)
        reg = vhcover.VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                                       key_ring=ring, issuer="cv-vhc",
                                       id_fn=demo_idseam._counter())
        q = reg.quote(days=30, age_years=35, day=1)
        self.assertEqual(q["quote_id"], "Q-00000000")
        # and the default path still mints secrets-shaped uniques
        reg2 = vhcover.VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                                        key_ring=ring, issuer="cv-vhc")
        q1 = reg2.quote(days=30, age_years=35, day=1)
        q2 = reg2.quote(days=30, age_years=35, day=1)
        self.assertNotEqual(q1["quote_id"], q2["quote_id"])

    def test_file_emission(self):
        with tempfile.TemporaryDirectory() as td:
            original = demo_idseam.OUT
            demo_idseam.OUT = os.path.join(td, "idseamproof.json")
            try:
                demo_idseam.main()
                with open(demo_idseam.OUT, encoding="utf-8") as fh:
                    written = json.load(fh)
                self.assertEqual(written["sha256"],
                                 demo_idseam.run_demo()["sha256"])
            finally:
                demo_idseam.OUT = original


if __name__ == "__main__":
    unittest.main()
