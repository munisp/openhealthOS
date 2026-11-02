"""test_bedboard.py — the rounding list answers 'who needs what
today'; device days are the denominators."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "teleread"))

from teleicu import BedBoard, TeleReadError  # noqa: E402


class TestAdmissionAndDevices(unittest.TestCase):
    def setUp(self):
        self.b = BedBoard("icu-1")
        self.b.admit("p1", bed="B3", day=0)

    def test_double_admit_and_ghost_discharge_refuse(self):
        with self.assertRaises(TeleReadError):
            self.b.admit("p1", bed="B4", day=1)
        with self.assertRaises(TeleReadError):
            self.b.admit("p2", bed="", day=1)

    def test_device_sequence_is_honest(self):
        self.b.device_start("p1", "ventilator", day=0)
        with self.assertRaises(TeleReadError):
            self.b.device_start("p1", "ventilator", day=1)
        with self.assertRaises(TeleReadError):
            self.b.device_end("p1", "central-line", day=1)
        with self.assertRaises(TeleReadError):
            self.b.device_end("p1", "ventilator", day=-1)
        self.b.device_end("p1", "ventilator", day=4)

    def test_discharge_with_open_device_refuses(self):
        self.b.device_start("p1", "central-line", day=0)
        with self.assertRaises(TeleReadError):
            self.b.discharge("p1", day=5, outcome="survived")
        self.b.device_end("p1", "central-line", day=5)
        self.b.discharge("p1", day=5, outcome="survived")
        with self.assertRaises(TeleReadError):
            self.b.discharge("p1", day=6, outcome="survived")


class TestMustDos(unittest.TestCase):
    def setUp(self):
        self.b = BedBoard("icu-1")
        self.b.admit("p1", bed="B3", day=0)

    def test_applicability_follows_open_devices(self):
        with self.assertRaises(TeleReadError):   # not ventilated
            self.b.record_check("p1", "sedation-interruption", day=1,
                                by="icu-1")
        self.b.device_start("p1", "ventilator", day=0)
        out = self.b.record_check("p1", "sedation-interruption", day=1,
                                  by="icu-1")
        self.assertEqual(out["outcome"], "done")

    def test_contraindicated_requires_reason(self):
        self.b.device_start("p1", "ventilator", day=0)
        with self.assertRaises(TeleReadError):
            self.b.record_check("p1", "sedation-interruption", day=1,
                                by="icu-1", outcome="contraindicated")
        out = self.b.record_check("p1", "sedation-interruption", day=1,
                                  by="icu-1", outcome="contraindicated",
                                  reason="RASS -4, neuro checks q1h")
        self.assertEqual(out["reason"], "RASS -4, neuro checks q1h")
        with self.assertRaises(TeleReadError):   # once per day
            self.b.record_check("p1", "sedation-interruption", day=1,
                                by="icu-1")

    def test_rounding_list_most_undone_first(self):
        self.b.device_start("p1", "ventilator", day=0)
        self.b.admit("p2", bed="B1", day=0)   # no devices: 2 must-dos
        rl = self.b.rounding_list(as_of=1)
        # p1: ventilated → 5 applicable (3 vent + 2 universal)
        self.assertEqual(rl[0]["patient_id"], "p1")
        self.assertEqual(rl[0]["n_open"], 5)
        self.assertEqual(rl[1]["n_open"], 2)
        self.b.record_check("p1", "sedation-interruption", day=1,
                            by="icu-1")
        self.b.record_check("p1", "spontaneous-breathing-trial",
                            day=1, by="icu-1")
        self.b.record_check("p1", "head-of-bed-30", day=1, by="icu-1")
        # equal open counts: the open ventilator day still ranks p1
        # first — device days break ties toward the sicker patient
        rl2 = self.b.rounding_list(as_of=1)
        self.assertEqual(rl2[0]["patient_id"], "p1")
        self.assertEqual(rl2[0]["n_open"], 2)
        # work p1 fully down and p2 leads the list
        self.b.record_check("p1", "dvt-prophylaxis-review", day=1,
                            by="icu-1")
        self.b.record_check("p1", "family-update", day=1, by="icu-1")
        rl3 = self.b.rounding_list(as_of=1)
        self.assertEqual(rl3[0]["patient_id"], "p2")
        self.assertEqual(rl3[1]["n_open"], 0)


class TestDeviceDays(unittest.TestCase):
    def test_utilization_divides_by_patient_days(self):
        b = BedBoard("icu-1")
        b.admit("p1", bed="B1", day=0)
        b.device_start("p1", "central-line", day=0)
        b.device_end("p1", "central-line", day=4)
        b.admit("p2", bed="B2", day=0)
        b.device_start("p2", "central-line", day=0)  # still open
        rep = b.device_report(as_of=10)
        self.assertEqual(rep["patient_days"], 20)
        self.assertEqual(rep["device_days"]["central-line"], 4 + 10)
        self.assertEqual(rep["utilization"]["central-line"],
                         round(14 / 20, 3))


if __name__ == "__main__":
    unittest.main()
