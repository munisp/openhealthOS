import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("pacs", "viewerlaunch"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from pacs import StudyRegistry, PACSError  # noqa: E402
from viewerlaunch import (ViewerLauncher, ViewerError,  # noqa: E402
                          VIEWER_ENGINES, VIEWER_VERSION)


def make_registry():
    reg = StudyRegistry()
    reg.register_study(study_uid="ST-1", accession="ACC-1",
                       patient_id="p-1", modality="CT",
                       exam_protocol="ct-chest", priority="routine",
                       facility="lagos", day=1)
    return reg


class TestLaunchGates(unittest.TestCase):
    def test_no_viewer_endpoint_refuses(self):
        v = ViewerLauncher(make_registry())
        with self.assertRaises(ViewerError):
            v.launch_study("ST-1", reader_id="r-1",
                           purpose="diagnosis", day=1)

    def test_unknown_study_refuses(self):
        v = ViewerLauncher(make_registry(),
                           viewer_base="https://viewer.example")
        with self.assertRaises(PACSError):
            v.launch_study("ST-NOPE", reader_id="r-1",
                           purpose="diagnosis", day=1)

    def test_purpose_mandatory(self):
        v = ViewerLauncher(make_registry(),
                           viewer_base="https://viewer.example")
        with self.assertRaises(ViewerError):
            v.launch_study("ST-1", reader_id="r-1", purpose="", day=1)

    def test_slide_without_tile_source_refuses(self):
        v = ViewerLauncher(slide_base="https://tiles.example")
        with self.assertRaises(ViewerError):
            v.launch_slide("SL-1", "", reader_id="p-1",
                           purpose="frozen review", day=1)
        with self.assertRaises(ViewerError):
            ViewerLauncher().launch_slide(
                "SL-1", "SL-1.dzi", reader_id="p-1",
                purpose="frozen review", day=1)


class TestManifests(unittest.TestCase):
    def test_study_manifest_carries_study_and_engine(self):
        v = ViewerLauncher(make_registry(),
                           viewer_base="https://viewer.example")
        out = v.launch_study("ST-1", reader_id="r-1",
                             purpose="diagnosis", day=1, case_id="RC-1")
        m = out["manifest"]
        self.assertEqual(m["engine"], "ohif")
        self.assertIn("StudyInstanceUIDs=ST-1", m["url"])
        self.assertEqual(m["viewer_version"], VIEWER_VERSION)
        self.assertIn("key_images", m)

    def test_slide_manifest(self):
        v = ViewerLauncher(slide_base="https://tiles.example")
        out = v.launch_slide("SL-1", "SL-1.dzi", reader_id="p-1",
                             purpose="histology", day=2)
        self.assertEqual(out["manifest"]["engine"], "openseadragon")
        self.assertEqual(out["manifest"]["url"],
                         "https://tiles.example/SL-1.dzi")

    def test_engines_are_clean_licence_references(self):
        for eng in VIEWER_ENGINES.values():
            self.assertIn(eng["license"], ("Apache-2.0",
                                           "BSD-3-Clause"))
            self.assertTrue(eng["upstream"].startswith("https://"))


class TestAuditAndCoverage(unittest.TestCase):
    def test_launch_audit_append_only(self):
        v = ViewerLauncher(make_registry(),
                           viewer_base="https://viewer.example")
        v.launch_study("ST-1", reader_id="r-1", purpose="dx", day=1)
        v.launch_study("ST-1", reader_id="r-2", purpose="second read",
                       day=2)
        self.assertEqual(len(v.sessions), 2)
        self.assertEqual(v.sessions_for("ST-1")[1]["reader"], "r-2")
        self.assertTrue(v.viewed_by("ST-1", "r-2"))
        self.assertFalse(v.viewed_by("ST-1", "r-9"))

    def test_coverage_report_names_unviewed_verifications(self):
        v = ViewerLauncher(make_registry(),
                           viewer_base="https://viewer.example")
        v.launch_study("ST-1", reader_id="r-1", purpose="dx", day=1)
        reports = [
            {"case_id": "RC-1", "reader": "r-1", "state": "verified"},
            {"case_id": "RC-2", "reader": "r-2", "state": "verified"},
            {"case_id": "RC-3", "reader": "r-3", "state": "draft"},
        ]
        rep = v.coverage_report(reports, {"RC-1": "ST-1",
                                          "RC-2": "ST-2",
                                          "RC-3": "ST-3"})
        self.assertEqual(rep["verified_with_artifact"], 2)
        self.assertEqual(rep["unviewed_cases"], ["RC-2"])
        self.assertEqual(rep["viewed_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
