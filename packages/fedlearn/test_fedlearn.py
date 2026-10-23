"""Federated learning: aggregate-only doctrine, fail-closed release."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fedlearn  # noqa: E402


class TestLocalUpdate(unittest.TestCase):
    def test_counts_only(self):
        evs = [{"dx": "B50.9"}] * 7 + [{"dx": "A15.0"}] * 3
        h = fedlearn.local_update(evs, lambda e: "W42:" + e["dx"])
        self.assertEqual(h, {"W42:B50.9": 7, "W42:A15.0": 3})

    def test_non_coarse_key_refused(self):
        with self.assertRaises(ValueError):
            fedlearn.local_update([{"x": 1}], lambda e: {"raw": e})


class TestMerge(unittest.TestCase):
    def test_sums_across_facilities(self):
        m = fedlearn.merge([{"W42:B50.9": 7}, {"W42:B50.9": 5, "W42:A15.0": 3}])
        self.assertEqual(m["W42:B50.9"], 12)
        self.assertEqual(m["W42:A15.0"], 3)

    def test_row_leak_structurally_refused(self):
        # a facility "update" carrying a raw string value is a leak; refuse
        with self.assertRaises(ValueError):
            fedlearn.merge([{"W42:B50.9": "PAT-0001,wrote,note"}])
        with self.assertRaises(ValueError):
            fedlearn.merge([{"W42:B50.9": {"patient": "x"}}])


class TestRelease(unittest.TestCase):
    def test_small_cells_suppressed_and_reported(self):
        h = {"W42:B50.9": 12, "W42:RARE1": 2, "W42:RARE2": 1}
        r = fedlearn.release(h, min_cell=5)
        self.assertEqual(r["released"], {"W42:B50.9": 12})
        self.assertEqual(r["suppressed_cells"], 2)

    def test_release_gate_is_cell_floor_not_row_kanonymity(self):
        # Pinned doctrine (caught by a live test): aggregate cells are
        # dimensions, not patient quasi-identifiers — the release gate
        # for histograms is the cell-size floor, and it reports itself.
        r = fedlearn.release({"W42:B50.9": 50, "W42:A15.0": 40}, min_cell=5)
        self.assertEqual(len(r["released"]), 2)
        self.assertEqual(r["gate"], "cell-size-floor")
        self.assertEqual(r["k"], 5)


if __name__ == "__main__":
    unittest.main()
