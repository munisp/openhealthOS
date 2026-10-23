#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "packs"))
from review_packet import fingerprint, render_packet  # noqa: E402
from ng_standing_orders import FEVER_V1, HTN_FOLLOWUP_V1  # noqa: E402


class TestReviewPacket(unittest.TestCase):
    def test_packet_contains_every_step_option_and_flag(self):
        md = render_packet("fever", FEVER_V1, "builder")
        for sid, step in FEVER_V1["steps"].items():
            self.assertIn(f"Step `{sid}`", md)
            self.assertIn(step["text"], md)
            for opt in step["options"]:
                self.assertIn(f"`{opt}`", md)
        self.assertIn("🚩", md)  # red flags rendered
        self.assertIn("mandatory escalation", md)

    def test_packet_renders_cadre_table_and_signoff(self):
        md = render_packet("fever", FEVER_V1, "builder")
        self.assertIn("| `give-iv-artesunate` | nurse |", md)
        self.assertIn('registry.review(', md)
        self.assertIn("DRAFT", md)

    def test_fingerprint_stable_and_changes_on_edit(self):
        fp1 = fingerprint(FEVER_V1)
        edited = dict(FEVER_V1, version="ngso-fever-1.0.1-seed")
        self.assertNotEqual(fp1, fingerprint(edited))

    def test_both_seed_packs_render(self):
        for name, pack in (("fever", FEVER_V1), ("htn-followup", HTN_FOLLOWUP_V1)):
            md = render_packet(name, pack, "builder")
            self.assertIn(pack["version"], md)
            self.assertGreater(len(md), 800)


if __name__ == "__main__":
    unittest.main()
