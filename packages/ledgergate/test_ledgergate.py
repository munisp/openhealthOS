"""test_ledgergate.py — ledgergate fixture and real-tree tests (ADR-0087)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from ledgergate import VERSION, audit  # noqa: E402

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _readme(*rows):
    """Build a README fixture from row fragments."""
    body = "# fixture\n\n| # | Feature | Detail |\n|---|---|---|\n"
    return body + "\n".join(rows) + "\n"


def _fixture(text):
    return audit(".", read=lambda root, path: text)


class TestFixtureLedgers(unittest.TestCase):
    def test_clean_ledger_is_green(self):
        r = _fixture(_readme("| 1 | alpha | x |", "| 2 | beta | x |", "| 3 | gamma | x |"))
        self.assertTrue(r["summary"]["ledger_ok"])
        self.assertEqual(r["verdicts"], [])
        self.assertEqual(r["summary"]["rows"], 3)
        self.assertEqual(r["summary"]["max_number"], 3)

    def test_duplicate_is_red_and_names_both_claimants(self):
        r = _fixture(_readme("| 1 | alpha | x |", "| 2 | beta | x |", "| 2 | gamma | x |"))
        self.assertFalse(r["summary"]["ledger_ok"])
        self.assertEqual(r["summary"]["duplicates"], [2])
        reds = [v for v in r["verdicts"] if v["kind"] == "duplicate"]
        self.assertEqual(len(reds), 1)
        self.assertIn("beta", reds[0]["detail"])
        self.assertIn("gamma", reds[0]["detail"])

    def test_gap_is_amber_and_stated(self):
        r = _fixture(_readme("| 1 | alpha | x |", "| 3 | gamma | x |"))
        self.assertTrue(r["summary"]["ledger_ok"])
        self.assertEqual(r["summary"]["gaps"], [2])
        ambers = [v for v in r["verdicts"] if v["kind"] == "gap"]
        self.assertEqual(len(ambers), 1)
        self.assertIn("stated", ambers[0]["detail"])

    def test_out_of_order_is_red(self):
        r = _fixture(_readme("| 1 | alpha | x |", "| 3 | gamma | x |", "| 2 | beta | x |"))
        self.assertFalse(r["summary"]["ledger_ok"])
        self.assertEqual(r["summary"]["out_of_order"], [2])
        reds = [v for v in r["verdicts"] if v["kind"] == "out_of_order"]
        self.assertIn("line 7", reds[0]["detail"])

    def test_subrow_after_parent_is_accepted(self):
        r = _fixture(_readme("| 1 | alpha | x |", "| 1a | alpha-plus | x |", "| 2 | beta | x |"))
        self.assertTrue(r["summary"]["ledger_ok"])
        self.assertEqual(r["verdicts"], [])
        self.assertEqual(r["summary"]["sub_rows"], 1)

    def test_orphan_subrow_is_red(self):
        r = _fixture(_readme("| 1 | alpha | x |", "| 5a | lost | x |", "| 2 | beta | x |"))
        self.assertFalse(r["summary"]["ledger_ok"])
        self.assertEqual(r["summary"]["orphan_subrows"], ["5a"])

    def test_suffix_sequence_not_starting_at_a_is_amber(self):
        r = _fixture(_readme("| 1 | alpha | x |", "| 1b | beta-plus | x |"))
        self.assertTrue(r["summary"]["ledger_ok"])
        self.assertEqual(r["summary"]["suffix_anomalies"], ["1b"])

    def test_unreadable_readme_is_a_verdict_not_a_crash(self):
        def boom(root, path):
            raise OSError("gone")
        r = audit(".", read=boom)
        self.assertFalse(r["summary"]["ledger_ok"])
        self.assertTrue(r["summary"]["error"])
        self.assertEqual(r["summary"]["red_verdicts"], 1)


class TestRealTree(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = audit(REPO_ROOT)
        cls.summary = cls.result["summary"]

    def test_live_ledger_is_ok_with_no_reds(self):
        self.assertTrue(self.summary["ledger_ok"])
        self.assertEqual(self.summary["red_verdicts"], 0)

    def test_census_pins(self):
        # wave-23 normalised ledger: 228 parent rows (incl. this wave's
        # own row), 3 sub-rows, no
        # duplicates, no gaps, fully ascending
        self.assertEqual(self.summary["rows"], 228)
        self.assertEqual(self.summary["sub_rows"], 3)
        self.assertEqual(self.summary["max_number"], 228)
        self.assertEqual(self.summary["duplicates"], [])
        self.assertEqual(self.summary["gaps"], [])
        self.assertEqual(self.summary["out_of_order"], [])

    def test_subrows_attach_and_sequence(self):
        self.assertEqual(self.summary["orphan_subrows"], [])
        self.assertEqual(self.summary["suffix_anomalies"], [])
        labels = [f"{e['number']}{e['suffix']}" for e in self.result["entries"]
                  if e["suffix"] is not None]
        self.assertEqual(labels, ["8a", "71a", "94a"])

    def test_no_amber_verdicts_on_the_live_tree(self):
        self.assertEqual(self.summary["amber_verdicts"], 0)
        self.assertEqual(self.result["verdicts"], [])


if __name__ == "__main__":
    unittest.main()
