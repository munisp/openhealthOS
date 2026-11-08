"""Portal demo seed tests."""

import json
import os
import tempfile
import unittest

import demo_portal


class TestPortalDemo(unittest.TestCase):
    def test_deterministic_and_withheld(self):
        a = demo_portal.seed_record_payload()
        b = demo_portal.seed_record_payload()
        self.assertEqual(a, b)
        preg = next(s for s in a["sections"] if s["key"] == "pregnancy")
        self.assertEqual(preg["state"], "withheld-by-consent")

    def test_share_link_absent_is_stated(self):
        p = demo_portal.seed_record_payload()
        self.assertIsNone(p["share_link"])   # stated, not faked

    def test_export_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = demo_portal.export_record(os.path.join(td, "r.json"))
            back = json.load(open(path))
        self.assertIn("sha256", back)


if __name__ == "__main__":
    unittest.main()
