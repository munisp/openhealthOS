"""Tests for the shellproof demo payload."""

import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))

import demo_shellproof  # noqa: E402
import shellproof  # noqa: E402
import proofindex  # noqa: E402

REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))


class TestShellproofDemo(unittest.TestCase):
    def test_determinism(self):
        a = demo_shellproof.run_demo()
        b = demo_shellproof.run_demo()
        self.assertEqual(a, b)

    def test_stamp_contract(self):
        p = demo_shellproof.run_demo()
        body = {k: v for k, v in p.items() if k != "sha256"}
        self.assertEqual(p["sha256"], demo_shellproof._stamp(body))

    def test_payload_shape(self):
        p = demo_shellproof.run_demo()
        self.assertEqual(p["version"], "shellproof-demo@1.0.0")
        self.assertEqual([d["severity"] for d in p["drills"]],
                         ["green", "red", "red", "amber"])
        pre = p["pre_wave_findings"]
        self.assertEqual(len(pre["install_killers"]), 1)
        self.assertEqual(pre["install_killers"][0]["path"],
                         "apps/pwa/design-tokens.css")
        self.assertEqual(len(pre["uncached"]), 13)
        self.assertEqual(len(pre["unregistered_pages"]), 3)

    def test_gate_is_real_install_killer_named(self):
        p = demo_shellproof.run_demo()
        killer = p["drills"][1]
        self.assertIn("apps/tokens.css", killer["verdict"])

    def test_live_audit_matches_real_tree(self):
        """The demo's live audit IS the real tree — pin it."""
        p = demo_shellproof.run_demo()
        payloads = [x["path"] for x in proofindex.PAYLOADS
                    if x["path"].startswith("apps/pwa/")]
        live = shellproof.audit(REPO_ROOT, payloads=payloads)
        self.assertEqual(p["live_audit"]["summary"], live["summary"])
        self.assertEqual(p["live_audit"]["install_killers"], [])
        self.assertEqual(p["live_audit"]["uncached"], [])

    def test_file_emission(self):
        demo_shellproof.main()
        with open(demo_shellproof.OUT, encoding="utf-8") as fh:
            back = json.load(fh)
        self.assertEqual(back["version"], demo_shellproof.DEMO_VERSION)
        body = {k: v for k, v in back.items() if k != "sha256"}
        self.assertEqual(back["sha256"], demo_shellproof._stamp(body))


if __name__ == "__main__":
    unittest.main()
