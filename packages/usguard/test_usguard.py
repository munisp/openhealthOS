import os
import sys
import unittest

for pkg in ("teleread",):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from usguard import (UltrasoundQA, ANOMALY_PLANES,
                     ANOMALY_PROTOCOL_VERSION)


def mk_qa():
    r = ReaderRegistry()
    r.register("us-1", licence_id="U1", licence_expiry_day=400,
               scopes=("us-overreader",))
    qa = UltrasoundQA(r)
    qa.register_scan("S1", patient_id="p1", sonographer="son-1",
                     facility="phc-1", gestational_weeks=21,
                     image_ref="img://s1", day=0)
    qa.register_scan("S2", patient_id="p2", sonographer="son-1",
                     facility="phc-1", gestational_weeks=21,
                     image_ref="img://s2", day=1)
    return qa


ALL_OK = {p: "adequate" for p in ANOMALY_PLANES}


class TestAnomalyChecklist(unittest.TestCase):
    def setUp(self):
        self.qa = mk_qa()

    def test_every_plane_accounted_for(self):
        short = dict(ALL_OK)
        del short["rvot"]
        with self.assertRaises(TeleReadError):
            self.qa.record_anomaly_checklist("S1", planes=short, day=0,
                                             by="son-1")
        with self.assertRaises(TeleReadError):  # unknown plane
            self.qa.record_anomaly_checklist(
                "S1", planes={**ALL_OK, "vibes-view": "adequate"},
                day=0, by="son-1")
        with self.assertRaises(TeleReadError):  # bad state
            self.qa.record_anomaly_checklist(
                "S1", planes={**ALL_OK, "rvot": "fine"}, day=0,
                by="son-1")

    def test_ga_window_is_fail_closed(self):
        self.qa.register_scan("S9", patient_id="p9", sonographer="son-1",
                              facility="phc-1", gestational_weeks=12,
                              image_ref="img://s9", day=0)
        with self.assertRaises(TeleReadError):
            self.qa.record_anomaly_checklist("S9", planes=ALL_OK, day=0,
                                             by="son-1")

    def test_unsigned_and_rewrite_refuse(self):
        with self.assertRaises(TeleReadError):
            self.qa.record_anomaly_checklist("S1", planes=ALL_OK, day=0,
                                             by="")
        self.qa.record_anomaly_checklist("S1", planes=ALL_OK, day=0,
                                         by="son-1")
        with self.assertRaises(TeleReadError):
            self.qa.record_anomaly_checklist("S1", planes=ALL_OK, day=1,
                                             by="son-1")

    def test_gaps_earn_completion_recall(self):
        planes = dict(ALL_OK)
        planes["rvot"] = "not-visualized"
        planes["spine-axial"] = "inadequate"
        out = self.qa.record_anomaly_checklist("S1", planes=planes,
                                               day=0, by="son-1")
        self.assertFalse(out["complete"])
        self.assertEqual(out["gaps"], ["rvot", "spine-axial"])
        self.assertEqual(out["protocol"], ANOMALY_PROTOCOL_VERSION)
        self.qa.record_anomaly_checklist("S2", planes=ALL_OK, day=1,
                                         by="son-1")
        inc = self.qa.incomplete_anomaly_scans()
        self.assertEqual([i["scan_id"] for i in inc], ["S1"])
        rep = self.qa.anomaly_report()
        self.assertEqual(rep["checklists"], 2)
        self.assertEqual(rep["complete"], 1)
        self.assertEqual(rep["completeness_rate"], 0.5)
        self.assertEqual(rep["per_plane_gaps"],
                         {"rvot": 1, "spine-axial": 1})


if __name__ == "__main__":
    unittest.main()
