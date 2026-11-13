"""Tests for adrchain — the ADR chain integrity gate (ADR-0086)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import adrchain


def _files(**named):
    """Build a fixture tree: named = {filename: content}."""
    def listdir(root, adr_dir):
        return sorted(named)
    def read(root, path):
        name = path.rsplit("/", 1)[-1]
        return named[name]
    return listdir, read


GOOD = "# ADR-{n:04d}: Decision {n}\n\nStatus: accepted (2026-01-01)\n\nBody.\n"


class TestFixtureTree(unittest.TestCase):

    def audit(self, **named):
        listdir, read = _files(**named)
        return adrchain.audit(".", listdir=listdir, read=read)

    def test_clean_chain_is_green(self):
        report = self.audit(**{f"adr-{n:04d}-decision-{n}.md": GOOD.format(n=n) for n in (1, 2, 3)})
        self.assertTrue(report["summary"]["chain_ok"])
        self.assertEqual(report["summary"]["files"], 3)
        self.assertEqual(report["summary"]["distinct_numbers"], 3)
        self.assertEqual(report["summary"]["duplicates"], 0)
        self.assertEqual(report["summary"]["gaps"], 0)
        self.assertEqual(report["duplicates"], [])
        self.assertEqual(report["gaps"], [])

    def test_duplicate_number_is_red_and_names_claimants(self):
        report = self.audit(**{
            "adr-0060-first-decision.md": GOOD.format(n=60),
            "adr-0060-second-decision.md": GOOD.format(n=60),
            "adr-0061-third-decision.md": GOOD.format(n=61),
        })
        self.assertFalse(report["summary"]["chain_ok"])
        self.assertEqual(len(report["duplicates"]), 1)
        dup = report["duplicates"][0]
        self.assertEqual(dup["number"], 60)
        self.assertEqual(dup["claimants"], [
            "docs/architecture/adr-0060-first-decision.md",
            "docs/architecture/adr-0060-second-decision.md",
        ])
        reds = [v for e in report["files"] for v in e["verdicts"] if v["kind"] == "duplicate-number"]
        self.assertEqual(len(reds), 2)  # both claimants marked

    def test_gap_is_amber_stated_not_red(self):
        report = self.audit(**{
            "adr-0001-one.md": GOOD.format(n=1),
            "adr-0003-three.md": GOOD.format(n=3),
        })
        self.assertTrue(report["summary"]["chain_ok"])  # gaps do not break the chain
        self.assertEqual(report["gaps"], [2])
        self.assertEqual(report["summary"]["gaps"], 1)

    def test_number_mismatch_between_filename_and_headline_is_red(self):
        report = self.audit(**{
            "adr-0007-renamed.md": "# ADR-0009: Was renamed but headline never updated\n\nStatus: accepted\n",
        })
        self.assertFalse(report["summary"]["chain_ok"])
        v = report["files"][0]["verdicts"][0]
        self.assertEqual(v["kind"], "number-mismatch")
        self.assertIn("0007", v["detail"])
        self.assertIn("0009", v["detail"])

    def test_missing_status_is_amber(self):
        report = self.audit(**{
            "adr-0001-one.md": "# ADR-0001: No status here\n\nBody only.\n",
        })
        self.assertTrue(report["summary"]["chain_ok"])  # amber, not red
        self.assertEqual(report["files"][0]["status"], None)
        self.assertEqual(report["summary"]["amber_verdicts"], 1)

    def test_inline_status_form_accepted(self):
        report = self.audit(**{
            "adr-0001-one.md": "# ADR-0001: Old style\n\nDate: 2026-10-20 · Status: accepted\n\nBody.\n",
        })
        self.assertEqual(report["files"][0]["status"], "declared")

    def test_missing_headline_is_red(self):
        report = self.audit(**{
            "adr-0001-one.md": "No headline at all.\n\nStatus: accepted\n",
        })
        self.assertFalse(report["summary"]["chain_ok"])
        self.assertEqual(report["files"][0]["verdicts"][0]["kind"], "no-title")

    def test_unreadable_directory_is_a_verdict_not_a_crash(self):
        def bad_listdir(root, adr_dir):
            raise FileNotFoundError(adr_dir)
        report = adrchain.audit(".", listdir=bad_listdir, read=lambda r, p: "")
        self.assertFalse(report["summary"]["chain_ok"])
        self.assertIn("unreadable", report["error"])


class TestRealTree(unittest.TestCase):
    """Meta gate: the live docs/architecture chain must be clean."""

    @classmethod
    def setUpClass(cls):
        root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
        cls.report = adrchain.audit(root)

    def test_chain_is_ok(self):
        bad = [(e["file"], v) for e in self.report["files"]
               for v in e["verdicts"] if v["severity"] == "red"]
        self.assertEqual(bad, [])
        self.assertTrue(self.report["summary"]["chain_ok"])

    def test_census_pins(self):
        s = self.report["summary"]
        self.assertEqual(s["files"], 89)
        self.assertEqual(s["distinct_numbers"], 89)
        self.assertEqual(s["max_number"], 89)
        self.assertEqual(self.report["gaps"], [])
        self.assertEqual(self.report["duplicates"], [])

    def test_every_file_declares_status(self):
        missing = [e["file"] for e in self.report["files"] if e["status"] is None]
        self.assertEqual(missing, [])

    def test_every_file_has_matching_headline(self):
        for e in self.report["files"]:
            self.assertIsNotNone(e["title"], e["file"])


if __name__ == "__main__":
    unittest.main()
