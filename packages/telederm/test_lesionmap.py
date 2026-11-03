import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for pkg in ("telederm", "teleread", "duelgrade", "drscreen",
            "aigov"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", pkg)))

from telederm import LesionMap, TeleReadError, LESIONMAP_VERSION


class TestLesionMap(unittest.TestCase):
    def setUp(self):
        self.lm = LesionMap()
        self.lm.enroll("p-1", risk_tier="high", day=0, by="derm-1")
        self.lm.map_lesion("p-1", "L-1", zone="back-upper",
                           image_ref="img://l1-day0", day=0,
                           by="derm-1", size_mm=4.0,
                           colours=("brown",))

    def test_change_flags_growth_colour_ulceration(self):
        out = self.lm.add_image("p-1", "L-1", image_ref="img://l1-d90",
                                day=90, by="derm-1", size_mm=6.5,
                                colours=("brown", "blue"),
                                ulcerated=True)
        blob = " ".join(out["flags"])
        self.assertIn("growth +2.5mm", blob)
        self.assertIn("blue", blob)
        self.assertIn("ulceration", blob)

    def test_unknown_zone_and_duplicate_refuse(self):
        with self.assertRaises(TeleReadError):
            self.lm.map_lesion("p-1", "L-2", zone="left elbow-ish",
                               image_ref="img://x", day=0, by="derm-1")
        with self.assertRaises(TeleReadError):
            self.lm.map_lesion("p-1", "L-1", zone="face",
                               image_ref="img://y", day=0, by="derm-1")

    def test_cannot_trend_unmapped_lesion(self):
        with self.assertRaises(TeleReadError):
            self.lm.add_image("p-1", "L-NOPE", image_ref="img://z",
                              day=10, by="derm-1")

    def test_surveillance_interval_from_last_complete_map(self):
        due = self.lm.surveillance_due(as_of=100)
        self.assertEqual(due, [])  # high tier = 180d
        self.lm.complete_map("p-1", day=100, by="derm-1")
        due = self.lm.surveillance_due(as_of=300)
        self.assertEqual(due[0]["days_overdue"], 20)

    def test_report_has_denominators(self):
        self.lm.enroll("p-2", risk_tier="standard", day=0,
                       by="derm-1")
        self.lm.add_image("p-1", "L-1", image_ref="img://l1-d90",
                          day=90, by="derm-1", size_mm=7.0)
        rep = self.lm.map_report()
        self.assertEqual(rep["patients_mapped"], 2)
        self.assertEqual(rep["lesions_changed"]["n"], 1)
        self.assertEqual(rep["lesions_changed"]["of"], 1)
        self.assertEqual(rep["version"], LESIONMAP_VERSION)


if __name__ == "__main__":
    unittest.main()
