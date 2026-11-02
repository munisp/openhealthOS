import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from staging import StagingRegistry, StagingError, STAGING_VERSION


class TestStaging(unittest.TestCase):
    def setUp(self):
        self.reg = StagingRegistry()

    def test_valid_stage_with_notation(self):
        out = self.reg.stage("p-1", site="colon", t="3", n="1a",
                             m="0", basis="pathological", by="path-1",
                             day=1, evidence_ref="SYN-1")
        self.assertEqual(out["notation"], "pT3N1AM0")
        self.assertEqual(out["version"], STAGING_VERSION)

    def test_impossible_category_refuses(self):
        with self.assertRaises(StagingError):
            self.reg.stage("p-1", site="colon", t="9", n="1a", m="0",
                           basis="clinical", by="dr-1", day=1,
                           evidence_ref="ct-1")
        with self.assertRaises(StagingError):
            self.reg.stage("p-1", site="lung", t="4a", n="0", m="0",
                           basis="clinical", by="dr-1", day=1,
                           evidence_ref="ct-1")

    def test_unknown_site_is_a_refusal_not_clearance(self):
        with self.assertRaises(StagingError):
            self.reg.stage("p-1", site="pancreas", t="1", n="0",
                           m="0", basis="clinical", by="dr-1", day=1,
                           evidence_ref="ct-1")

    def test_edition_is_part_of_the_stage(self):
        with self.assertRaises(StagingError):
            self.reg.stage("p-1", site="colon", t="3", n="1a", m="0",
                           basis="pathological", by="path-1", day=1,
                           evidence_ref="SYN-1", edition="ajcc-7")

    def test_missing_axis_and_evidence_refuse(self):
        with self.assertRaises(StagingError):
            self.reg.stage("p-1", site="colon", t="3", n="", m="0",
                           basis="clinical", by="dr-1", day=1,
                           evidence_ref="x")
        with self.assertRaises(StagingError):
            self.reg.stage("p-1", site="colon", t="3", n="1a", m="0",
                           basis="clinical", by="dr-1", day=1,
                           evidence_ref="")

    def test_restaging_is_append_only(self):
        self.reg.stage("p-1", site="colon", t="3", n="1a", m="0",
                       basis="pathological", by="path-1", day=1,
                       evidence_ref="SYN-1")
        self.reg.stage("p-1", site="colon", t="3", n="1a", m="1b",
                       basis="clinical", by="onc-1", day=90,
                       evidence_ref="ct-scan-90")
        cur = self.reg.current_stage("p-1", site="colon")
        self.assertTrue(cur["restaged"])
        self.assertEqual(len(cur["history"]), 2)
        self.assertEqual(cur["history"][0]["m"], "0")  # first stays
        self.assertEqual(cur["current"]["notation"], "cT3N1AM1B")

    def test_absence_is_not_stage_zero(self):
        with self.assertRaises(StagingError):
            self.reg.current_stage("p-never")


if __name__ == "__main__":
    unittest.main()
