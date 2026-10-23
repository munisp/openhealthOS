"""Free-text capture: structured drafts with honest provenance."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture  # noqa: E402


class TestCapture(unittest.TestCase):
    def test_structured_draft_with_real_codes(self):
        d = capture.capture("malaria, gave paracetamol 500mg, temp 38.9")
        self.assertEqual(d["source"], "free-text")
        dx = d["fields"]["diagnosis"][0]
        self.assertEqual(dx["code"], "B50.9")  # real ICD-10 snapshot
        self.assertEqual(d["fields"]["dose"]["value"], 500.0)
        self.assertEqual(d["fields"]["temperature_c"], 38.9)
        self.assertEqual(d["original"], "malaria, gave paracetamol 500mg, temp 38.9")

    def test_unmapped_mentions_are_listed_not_dropped(self):
        d = capture.capture("malaria, gave amodiaquine 200mg")
        self.assertTrue(any(m["text"] == "amodiaquine" for m in d["unmapped"]))

    def test_pii_in_narrative_flagged_not_stripped(self):
        d = capture.capture("malaria. Call 08035551234 about the result")
        kinds = [w["kind"] for w in d["warnings"]]
        self.assertIn("pii-in-narrative", kinds)
        self.assertIn("08035551234", d["original"])  # provenance intact

    def test_empty_extraction_is_honest(self):
        d = capture.capture("hello how are you")
        self.assertEqual(d["fields"], {})
        self.assertEqual(d["warnings"][0]["kind"], "nothing-extracted")


if __name__ == "__main__":
    unittest.main()
