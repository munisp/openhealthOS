import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
for pkg in ("teleecg", "teleread"):
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", pkg)))

from teleread import ReaderRegistry, TeleReadError
from teleecg import ECGService, HolterService, MachineEditTrail


def mk_svc():
    r = ReaderRegistry()
    r.register("card-1", licence_id="L1", licence_expiry_day=10**9,
               scopes=("cardiac-reader",))
    s = ECGService(r)
    s.record_tracing("T1", patient_id="p1", kind="12-lead",
                     device_id=None, waveform_ref="w1",
                     facility="phc-1", referrer="n-1", day=1)
    return s


class TestHolter(unittest.TestCase):
    def setUp(self):
        self.h = HolterService()
        self.h.start_monitor("M1", patient_id="p1", kind="holter-24h",
                             device_id="d-1", facility="phc-1", day=0)

    def test_usable_recording_analyzes_with_flags(self):
        out = self.h.end_monitor("M1", day=1, usable_hours=22)
        self.assertEqual(out["state"], "analyzable")
        self.h.annotate_episode("M1", kind="af", day=1, minute=300,
                                duration_s=45)
        a = self.h.record_analysis("M1", by="card-1", day=1,
                                   total_beats=110000, min_hr=48,
                                   avg_hr=76, max_hr=140,
                                   af_burden_pct=4.2, pause_max_s=1.2)
        self.assertTrue(any("stroke-risk" in f for f in a["flags"]))

    def test_insufficient_recording_is_a_repeat_not_a_negative(self):
        out = self.h.end_monitor("M1", day=1, usable_hours=3)
        self.assertEqual(out["state"], "insufficient-recording")
        with self.assertRaises(TeleReadError):
            self.h.record_analysis("M1", by="card-1", day=1,
                                   total_beats=1000, min_hr=40,
                                   avg_hr=60, max_hr=80)

    def test_episode_outside_tape_refused(self):
        self.h.end_monitor("M1", day=1, usable_hours=24)
        with self.assertRaises(TeleReadError):
            self.h.annotate_episode("M1", kind="af", day=1,
                                    minute=99999, duration_s=30)

    def test_report_has_denominators(self):
        self.h.end_monitor("M1", day=1, usable_hours=22)
        self.h.annotate_episode("M1", kind="patient-symptom", day=1,
                                minute=100, duration_s=10)
        self.h.record_analysis("M1", by="card-1", day=1,
                               total_beats=100000, min_hr=50,
                               avg_hr=70, max_hr=120,
                               af_burden_pct=2.0)
        self.h.start_monitor("M2", patient_id="p2", kind="holter-24h",
                             device_id="d-1", facility="phc-1", day=0)
        self.h.end_monitor("M2", day=1, usable_hours=4)
        rep = self.h.monitor_report()
        self.assertEqual(rep["analyzable_rate"]["n"], 1)
        self.assertEqual(rep["analyzable_rate"]["of"], 2)
        self.assertEqual(rep["af_detected"]["n"], 1)
        self.assertEqual(rep["af_detected"]["of"], 1)


class TestMachineEditTrail(unittest.TestCase):
    def test_unedited_machine_read_counts_as_agreement(self):
        svc = mk_svc()
        trail = MachineEditTrail(svc)
        trail.record_machine_interpretation(
            "T1", text="Sinus rhythm, normal ECG",
            codes=["sinus-rhythm", "normal"], sw_version="carto-v7.2")
        out = trail.record_final_interpretation(
            "T1", text="Sinus rhythm, normal ECG",
            codes=["sinus-rhythm", "normal"], by="card-1", day=2)
        self.assertFalse(out["edited"])

    def test_edits_named_both_ways(self):
        svc = mk_svc()
        trail = MachineEditTrail(svc)
        trail.record_machine_interpretation(
            "T1", text="Normal", codes=["normal"],
            sw_version="carto-v7.2")
        out = trail.record_final_interpretation(
            "T1", text="Sinus rhythm with ST depression V4-V6",
            codes=["sinus-rhythm", "st-depression"], by="card-1",
            day=2)
        self.assertTrue(out["edited"])
        self.assertEqual(out["codes_added"], ["sinus-rhythm",
                                              "st-depression"])
        self.assertEqual(out["codes_removed"], ["normal"])

    def test_final_needs_machine_baseline(self):
        svc = mk_svc()
        svc.record_tracing("T2", patient_id="p2", kind="12-lead",
                           device_id=None, waveform_ref="w2",
                           facility="phc-1", referrer="n-1", day=1)
        trail = MachineEditTrail(svc)
        with self.assertRaises(TeleReadError):
            trail.record_final_interpretation(
                "T2", text="x", codes=["normal"], by="card-1", day=2)

    def test_concordance_per_software_version(self):
        svc = mk_svc()
        svc.record_tracing("T2", patient_id="p2", kind="12-lead",
                           device_id=None, waveform_ref="w2",
                           facility="phc-1", referrer="n-1", day=1)
        trail = MachineEditTrail(svc)
        trail.record_machine_interpretation(
            "T1", text="n", codes=["normal"], sw_version="v7.2")
        trail.record_machine_interpretation(
            "T2", text="n", codes=["normal"], sw_version="v7.3")
        trail.record_final_interpretation(
            "T1", text="n", codes=["normal"], by="card-1", day=2)
        trail.record_final_interpretation(
            "T2", text="af", codes=["af"], by="card-1", day=2)
        rep = trail.concordance_report()
        self.assertEqual(rep["agreement_rate"], 0.5)
        self.assertEqual(rep["per_sw_version"]["v7.2"]
                         ["agreement_rate"], 1.0)
        self.assertEqual(rep["per_sw_version"]["v7.3"]
                         ["agreement_rate"], 0.0)


if __name__ == "__main__":
    unittest.main()
