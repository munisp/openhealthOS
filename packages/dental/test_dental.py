"""Tests for packages/dental — odontogram and dental charting."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dental import Odontogram, Error, PERMANENT, VALID_TEETH  # noqa: E402


class TestOdontogram(unittest.TestCase):
    def setUp(self):
        self.od = Odontogram("Pax-1")

    def test_valid_fdi(self):
        self.od.tooth(36).record("caries", by="dr-dent", day=1,
                                 surfaces=["occlusal"])
        self.assertEqual(self.od.tooth(36).current, "caries")

    def test_invalid_fdi_refused(self):
        with self.assertRaises(Error):
            self.od.tooth(19)
        with self.assertRaises(Error):
            self.od.tooth(49)

    def test_deciduous_teeth_valid(self):
        self.od.tooth(51).record("caries", by="dr-dent", day=1)
        self.assertIn(51, VALID_TEETH)

    def test_unknown_condition_refused(self):
        with self.assertRaises(Error):
            self.od.tooth(11).record("rotten", by="dr-dent", day=1)

    def test_unknown_surface_refused(self):
        with self.assertRaises(Error):
            self.od.tooth(11).record("caries", by="dr-dent", day=1,
                                     surfaces=["top"])

    def test_terminal_extraction_blocks_further_work(self):
        self.od.tooth(46).record("caries", by="dr-dent", day=1)
        self.od.tooth(46).record("extracted", by="dr-dent", day=5)
        with self.assertRaises(Error):
            self.od.tooth(46).record("filled", by="dr-dent", day=6)

    def test_correction_cites_entry(self):
        t = self.od.tooth(46)
        t.record("extracted", by="dr-dent", day=1)
        with self.assertRaises(Error):
            t.record("filled", by="dr-dent", day=2, amends=1)  # no reason
        fix = t.record("filled", by="dr-dent", day=2, amends=1,
                       note="wrong tooth keyed — 46 restored, not extracted")
        self.assertEqual(fix["amends"], 1)
        self.assertEqual(t.current, "filled")

    def test_snapshot_checksummed(self):
        self.od.tooth(11).record("caries", by="dr-dent", day=1)
        self.od.tooth(36).record("filled", by="dr-dent", day=1)
        snap = self.od.snapshot(day=1)
        self.assertEqual(snap["counts"]["caries"], 1)
        self.assertEqual(snap["counts"]["filled"], 1)
        self.assertEqual(len(snap["sha256"]), 64)

    def test_permanent_dentition_completeness(self):
        self.assertEqual(len(PERMANENT), 32)


if __name__ == "__main__":
    unittest.main()
