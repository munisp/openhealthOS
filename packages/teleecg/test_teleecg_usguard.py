import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "teleread"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devicereg"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usguard"))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from devicereg import DeviceRegistry
from teleecg import ECGService
from usguard import UltrasoundQA


def mk_readers():
    r = ReaderRegistry()
    r.register("card-1", licence_id="MDCN-C1", licence_expiry_day=400,
               scopes=("cardiac-reader",))
    r.register("us-1", licence_id="MDCN-U1", licence_expiry_day=400,
               scopes=("us-overreader",))
    return r


class TestECG(unittest.TestCase):
    def setUp(self):
        self.dev = DeviceRegistry()
        self.dev.register("ecg-1", device_type="ecg", serial="E1",
                          facility="phc-1", attested_by="m", day=0,
                          calibration_day=0)
        self.svc = ECGService(mk_readers(), self.dev)

    def test_waveform_required_and_device_flagged(self):
        with self.assertRaises(TeleReadError):
            self.svc.record_tracing("T1", patient_id="p1", kind="12-lead",
                                    device_id="ecg-1", waveform_ref="",
                                    facility="phc-1", referrer="n1", day=1)
        t = self.svc.record_tracing("T1", patient_id="p1", kind="12-lead",
                                    device_id="ecg-1", waveform_ref="w1",
                                    facility="phc-1", referrer="n-1",
                                    day=400)  # calibration stale (>365d)
        self.assertTrue(any("calibration" in f for f in t["flags"]))

    def test_full_flow_stemi_escalates(self):
        self.svc.record_tracing("T1", patient_id="p1", kind="12-lead",
                                device_id="ecg-1", waveform_ref="w1",
                                facility="phc-1", referrer="n-1", day=1)
        out = self.svc.submit_for_reading("T1", case_id="E1",
                                          priority="stat",
                                          question="chest pain",
                                          referrer="n-1", day=1)
        self.assertEqual(out["scope"], "cardiac-reader")
        r = self.svc.reading
        r.assign("E1", day=1, prefer="card-1")
        r.draft_report("E1", reader_id="card-1", findings="ST elevation",
                       impression="anterior STEMI", day=1,
                       critical_findings=("stemi",),
                       template_version="ecg-v1")
        r.verify_report("E1", reader_id="card-1", day=1)
        self.assertEqual(len(r.unacknowledged_criticals(2)), 1)


class TestUSGuard(unittest.TestCase):
    def setUp(self):
        self.qa = UltrasoundQA(mk_readers(), sample_percent=100)

    def test_ga_bounds(self):
        with self.assertRaises(TeleReadError):
            self.qa.register_scan("S1", patient_id="p1", sonographer="mw-1",
                                  facility="phc-1", gestational_weeks=2,
                                  image_ref="i1", day=0)

    def test_flagged_always_sampled(self):
        self.qa.register_scan("S1", patient_id="p1", sonographer="mw-1",
                              facility="phc-1", gestational_weeks=8,
                              image_ref="i1", day=0, flagged_abnormal=True)
        self.assertTrue(self.qa.needs_overread("S1"))
        out = self.qa.submit_overread("S1", case_id="U1", referrer="mw-1",
                                      day=0)
        self.assertEqual(out["priority"], "urgent")

    def test_outside_sample_refused(self):
        qa = UltrasoundQA(mk_readers(), sample_percent=1)
        qa.register_scan("S9", patient_id="p", sonographer="mw-1",
                         facility="phc-1", gestational_weeks=20,
                         image_ref="i", day=0)
        if not qa.needs_overread("S9"):
            with self.assertRaises(TeleReadError):
                qa.submit_overread("S9", case_id="U9", referrer="r", day=0)

    def test_concordance_stats(self):
        self.qa.register_scan("S1", patient_id="p1", sonographer="mw-1",
                              facility="phc-1", gestational_weeks=12,
                              image_ref="i1", day=0)
        self.qa.register_scan("S2", patient_id="p2", sonographer="mw-1",
                              facility="phc-1", gestational_weeks=24,
                              image_ref="i2", day=0)
        self.qa.record_concordance("S1", agrees=True)
        self.qa.record_concordance("S2", agrees=False,
                                   discrepancy="wrong dating")
        stats = self.qa.sonographer_concordance()
        self.assertEqual(stats["by_sonographer"]["mw-1"],
                         {"n": 2, "agreed": 1})


if __name__ == "__main__":
    unittest.main()
