import os
import sys
import unittest

for pkg in ("vitals",):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from vitals import VitalsIngest
from earlywarn import EarlyWarner, EarlyWarnError, news2, NEWS2_VERSION


class TestNews2Table(unittest.TestCase):
    def test_known_vectors(self):
        # all-normal scores zero
        s = news2(rr=16, spo2=98, temp_c=37.0, sbp=120, hr=72, avpu="A")
        self.assertEqual((s["score"], s["band"]), (0, "low"))
        self.assertFalse(s["escalate"])
        # classic deteriorating patient: rr 26 (3), spo2 92 (2) on O2
        # (+2), temp 39.2 (2), sbp 95 (2), hr 115 (2), A (0) = 13 high
        s = news2(rr=26, spo2=92, temp_c=39.2, sbp=95, hr=115, avpu="A",
                  on_oxygen=True)
        self.assertEqual((s["score"], s["band"]), (13, "high"))
        self.assertTrue(s["escalate"])

    def test_single_parameter_3_rule(self):
        # sbp 85 alone: total 3 = low band, but a 3-point parameter
        # demands review per the RCP rule
        s = news2(rr=15, spo2=97, temp_c=36.8, sbp=85, hr=70, avpu="A")
        self.assertEqual(s["band"], "low")
        self.assertEqual(s["single_param_3"], ["sbp"])
        self.assertTrue(s["escalate"])

    def test_spo2_scale2_hypercapnic(self):
        # scale 2: 90% on oxygen is the TARGET band, not a crisis
        s = news2(rr=18, spo2=90, temp_c=37.0, sbp=120, hr=80, avpu="A",
                  on_oxygen=True, spo2_scale2=True)
        self.assertEqual(s["parts"]["spo2"], 0)
        self.assertEqual(s["score"], 2)  # oxygen points only
        # 96% on oxygen on scale 2 is hyperoxia — penalized
        s = news2(rr=18, spo2=96, temp_c=37.0, sbp=120, hr=80, avpu="A",
                  on_oxygen=True, spo2_scale2=True)
        self.assertEqual(s["parts"]["spo2"], 2)

    def test_fail_closed(self):
        with self.assertRaises(EarlyWarnError):
            news2(rr=15, spo2=97, temp_c=36.8, sbp=120, hr=70,
                  avpu="alert-ish")


class TestEarlyWarner(unittest.TestCase):
    def setUp(self):
        self.vitals = VitalsIngest()
        self.ew = EarlyWarner(self.vitals)

    def _full_set(self, pid, day, *, rr=16, spo2=98, temp=37.0,
                  sbp=120, hr=72):
        for vital, val in (("respiratory-rate", rr), ("spo2", spo2),
                           ("temperature", temp), ("systolic-bp", sbp),
                           ("heart-rate", hr)):
            self.vitals.record(pid, vital, val, day=day,
                               ts=f"2026-11-02T{8 + day:02d}:00:00Z")

    def test_missing_vital_refuses(self):
        self._full_set("p1", 0)
        # drop one: record a second patient with no temperature
        self.vitals.record("p2", "spo2", 95, day=0,
                           ts="2026-11-02T08:00:00Z")
        with self.assertRaises(EarlyWarnError):
            self.ew.score_now("p2", day=0, ts="t", avpu="A")

    def test_trajectory_is_the_finding(self):
        self._full_set("p1", 0)
        one = self.ew.score_now("p1", day=0, ts="t0", avpu="A")
        self.assertEqual(one["score"], 0)
        t = self.ew.trend("p1")
        self.assertIsNone(t["direction"])  # abstains on one score
        # day 1: creeping; day 2: clearly deteriorating
        self._full_set("p1", 1, rr=22, spo2=94, hr=105)
        self.ew.score_now("p1", day=1, ts="t1", avpu="A")
        self._full_set("p1", 2, rr=27, spo2=91, temp=39.0, sbp=100,
                       hr=115)
        self.ew.score_now("p1", day=2, ts="t2", avpu="A")
        t = self.ew.trend("p1")
        self.assertEqual(t["direction"], "rising")
        self.assertTrue(t["crossed_band_upward"])
        self.assertEqual(t["band_now"], "high")
        det = self.ew.deteriorating(as_of=2)
        self.assertEqual(det[0]["patient_id"], "p1")

    def test_report_denominators(self):
        self._full_set("p1", 0)
        self._full_set("p2", 0, rr=28, spo2=90, sbp=88)
        self.ew.score_now("p1", day=0, ts="t", avpu="A")
        self.ew.score_now("p2", day=0, ts="t", avpu="V")
        rep = self.ew.report(as_of=0)
        self.assertEqual(rep["patients_scored"], 2)
        self.assertEqual(rep["latest_band_distribution"]["low"], 1)
        self.assertEqual(rep["latest_band_distribution"]["high"], 1)
        self.assertEqual(rep["deteriorating_now"], 1)
        self.assertEqual(rep["version"], NEWS2_VERSION)


if __name__ == "__main__":
    unittest.main()
