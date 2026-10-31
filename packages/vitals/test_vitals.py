import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devicereg"))
from devicereg import DeviceRegistry
from vitals import VitalsIngest, VitalsError


class TestVitals(unittest.TestCase):
    def setUp(self):
        self.reg = DeviceRegistry()
        self.reg.register("cuff-1", device_type="bp-cuff", serial="S1",
                          facility="phc-1", attested_by="m", day=0,
                          calibration_day=0)
        self.v = VitalsIngest(self.reg)

    def test_observation_shape(self):
        obs = self.v.record("p1", "systolic-bp", 120, day=10,
                            ts="2026-01-10T09:00", device_id="cuff-1")
        self.assertEqual(obs["resourceType"], "Observation")
        self.assertEqual(obs["code"]["coding"][0]["code"], "8480-6")
        self.assertEqual(obs["valueQuantity"]["value"], 120)
        self.assertTrue(obs["device"]["verified"])
        self.assertEqual(obs["performer_type"], "device")

    def test_implausible_refused_not_clamped(self):
        with self.assertRaises(VitalsError) as cm:
            self.v.record("p1", "heart-rate", 400, day=1, ts="t")
        self.assertIn("refused", str(cm.exception))
        with self.assertRaises(VitalsError):
            self.v.record("p1", "spo2", 30, day=1, ts="t")
        self.assertEqual(self.v.latest("p1", "heart-rate"), None)

    def test_manual_vs_device_provenance(self):
        obs = self.v.record("p1", "temperature", 38.5, day=1, ts="t",
                            performer="human")
        self.assertEqual(obs["performer_type"], "human")
        self.assertNotIn("device", obs)
        with self.assertRaises(VitalsError):
            self.v.record("p1", "temperature", 38.5, day=1, ts="t",
                          performer="device")  # device claim, no device_id

    def test_device_reading_requires_registry(self):
        bare = VitalsIngest()
        with self.assertRaises(VitalsError):
            bare.record("p1", "spo2", 97, day=1, ts="t", device_id="x")

    def test_stale_calibration_flag_rides_on_observation(self):
        obs = self.v.record("p1", "systolic-bp", 150, day=400, ts="t",
                            device_id="cuff-1")
        self.assertEqual(obs["flag"], "calibration-stale")

    def test_series_chronological_and_filterable(self):
        self.v.record("p1", "spo2", 95, day=10, ts="t2")
        self.v.record("p1", "spo2", 92, day=12, ts="t3")
        self.v.record("p1", "spo2", 97, day=5, ts="t1")
        s = self.v.series("p1", "spo2")
        self.assertEqual([o["valueQuantity"]["value"] for o in s],
                         [97, 95, 92])
        self.assertEqual(len(self.v.series("p1", "spo2", since_day=10)), 2)
        self.assertEqual(self.v.latest("p1", "spo2")["valueQuantity"]["value"], 92)

    def test_unknown_vital_and_nonnumeric_refused(self):
        with self.assertRaises(VitalsError):
            self.v.record("p1", "mood", 5, day=1, ts="t")
        with self.assertRaises(VitalsError):
            self.v.record("p1", "spo2", "high", day=1, ts="t")


if __name__ == "__main__":
    unittest.main()
