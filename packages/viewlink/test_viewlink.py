import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from viewlink import ViewerLauncher, ViewLinkError


class TestViewLink(unittest.TestCase):
    def setUp(self):
        self.v = ViewerLauncher(
            ohif_base="https://viewer.hosp.example/viewer",
            slide_base="https://slides.hosp.example/osd")

    def test_unconfigured_viewer_refuses_loudly(self):
        with self.assertRaises(ViewLinkError) as ctx:
            self.v.launch("ecg-canvas", artifact_ref="tr-1",
                          reader_id="r1", session_id="s1",
                          case_id="c1", day=1)
        self.assertIn("not configured", str(ctx.exception))

    def test_ohif_launch_url(self):
        out = self.v.launch("dicom-ohif", artifact_ref="1.2.3.4",
                            reader_id="r1", session_id="s1",
                            case_id="c1", day=1)
        self.assertEqual(out["url"], "https://viewer.hosp.example/"
                                     "viewer?StudyInstanceUIDs=1.2.3.4")
        self.assertNotIn("token", out["url"].lower())

    def test_slide_launch_url(self):
        out = self.v.launch("wsi-openseadragon", artifact_ref="slide-9",
                            reader_id="r1", session_id="s1",
                            case_id="c1", day=1)
        self.assertIn("slide=slide-9", out["url"])

    def test_launch_is_an_access_event(self):
        self.v.launch("dicom-ohif", artifact_ref="1.2.3.4",
                      reader_id="r1", session_id="s1", case_id="c1", day=1)
        log = self.v.access_log(case_id="c1")
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["reader_id"], "r1")
        self.assertTrue(log[0]["event_hash"])

    def test_missing_artifact_or_identity_refused(self):
        with self.assertRaises(ViewLinkError):
            self.v.launch("dicom-ohif", artifact_ref="", reader_id="r1",
                          session_id="s1", case_id="c1", day=1)
        with self.assertRaises(ViewLinkError):
            self.v.launch("dicom-ohif", artifact_ref="x", reader_id="",
                          session_id="s1", case_id="c1", day=1)

    def test_unknown_kind_refused(self):
        with self.assertRaises(ViewLinkError):
            self.v.launch("magic-3d", artifact_ref="x", reader_id="r1",
                          session_id="s1", case_id="c1", day=1)


if __name__ == "__main__":
    unittest.main()
