import unittest
from devicereg import (DeviceRegistry, DeviceError, DEVICE_TYPES,
                       CALIBRATION_VALID_DAYS, SILENCE_DAYS)


class TestDeviceReg(unittest.TestCase):
    def setUp(self):
        self.r = DeviceRegistry()
        self.r.register("cuff-1", device_type="bp-cuff", serial="SN-001",
                        facility="phc-1", attested_by="matron", day=0,
                        calibration_day=0)

    def test_register_and_verified_reading(self):
        st = self.r.reading_status("cuff-1", day=100)
        self.assertTrue(st["verified"])
        self.assertIsNone(st["flag"])

    def test_anonymous_and_duplicate_refused(self):
        with self.assertRaises(DeviceError):
            self.r.register("x", device_type="bp-cuff", serial="",
                            facility="f", attested_by="o", day=1,
                            calibration_day=0)
        with self.assertRaises(DeviceError):
            self.r.register("cuff-1", device_type="bp-cuff", serial="S2",
                            facility="f", attested_by="o", day=1,
                            calibration_day=0)
        with self.assertRaises(DeviceError):
            self.r.register("y", device_type="mood-ring", serial="S",
                            facility="f", attested_by="o", day=1,
                            calibration_day=0)

    def test_stale_calibration_flagged_not_blocked(self):
        st = self.r.reading_status("cuff-1",
                                   day=CALIBRATION_VALID_DAYS + 10)
        self.assertFalse(st["verified"])
        self.assertEqual(st["flag"], "calibration-stale")
        self.r.recalibrate("cuff-1", day=CALIBRATION_VALID_DAYS + 20,
                           technician="biomed-t")
        self.assertTrue(
            self.r.reading_status("cuff-1",
                                  day=CALIBRATION_VALID_DAYS + 21)["verified"])

    def test_retired_device_refuses_new_readings(self):
        self.r.retire("cuff-1", day=50, reason="cracked-cuff", officer="o")
        with self.assertRaises(DeviceError):
            self.r.reading_status("cuff-1", day=51)
        with self.assertRaises(DeviceError):
            self.r.recalibrate("cuff-1", day=51, technician="t")
        with self.assertRaises(DeviceError):
            self.r.retire("cuff-2", day=1, reason="ghost", officer="o")

    def test_retirement_needs_reason(self):
        with self.assertRaises(DeviceError):
            self.r.retire("cuff-1", day=50, reason="", officer="o")

    def test_silence_is_a_finding(self):
        self.r.note_reading("cuff-1", 10)
        sil = self.r.silent_devices(as_of=10 + SILENCE_DAYS)
        self.assertEqual(sil, [])
        sil = self.r.silent_devices(as_of=10 + SILENCE_DAYS + 1)
        self.assertEqual(len(sil), 1)
        self.assertFalse(sil[0]["never_reported"])

    def test_never_reported_device_surfaced(self):
        self.r.register("oxi-1", device_type="pulse-oximeter",
                        serial="SN-9", facility="phc-2",
                        attested_by="matron", day=0, calibration_day=0)
        sil = self.r.silent_devices(as_of=SILENCE_DAYS + 2)
        never = [s for s in sil if s["device_id"] == "oxi-1"]
        self.assertTrue(never[0]["never_reported"])

    def test_report_counts(self):
        self.r.register("old-1", device_type="scale", serial="S",
                        facility="phc-1", attested_by="o", day=0,
                        calibration_day=0)
        rep = self.r.report(as_of=CALIBRATION_VALID_DAYS + 1)
        self.assertEqual(rep["calibration_stale"], 2)
        self.assertEqual(rep["verified"], 0)


if __name__ == "__main__":
    unittest.main()
