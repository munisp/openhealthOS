import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from nephrology import DialysisUnit, NephroError  # noqa: E402


def make_unit():
    u = DialysisUnit("f")
    u.register_machine("M1")
    u.register_machine("M2", hbv_dedicated=True)
    u.register_machine("M3")
    u.register_patient("p1", dry_weight_kg=70, egfr=8.0, day=0)
    return u


class TestMachineFault(unittest.TestCase):
    def test_fault_interrupts_running_sessions_loudly(self):
        u = make_unit()
        u.start_session("S1", patient_id="p1", machine_id="M1",
                        dialyzer_id="D1", day=1)
        out = u.record_machine_fault("M1", day=1,
                                     description="blood leak alarm",
                                     by="rn-1")
        self.assertEqual(out["sessions_interrupted"], ["S1"])
        self.assertEqual(u.sessions["S1"]["state"], "interrupted")
        self.assertTrue(u.machines["M1"]["out_of_service"])

    def test_faulted_machine_refuses_new_session(self):
        u = make_unit()
        u.record_machine_fault("M1", day=1, description="pump failure",
                               by="rn-1")
        with self.assertRaises(NephroError):
            u.start_session("S2", patient_id="p1", machine_id="M1",
                            dialyzer_id="D1", day=1)

    def test_reseat_moves_interrupted_session_and_rechecks_hbv(self):
        u = make_unit()
        u.start_session("S1", patient_id="p1", machine_id="M1",
                        dialyzer_id="D1", day=1)
        u.record_machine_fault("M1", day=1, description="leak",
                               by="rn-1")
        out = u.reseat_session("S1", new_machine_id="M3", day=1,
                               by="rn-2")
        self.assertEqual(out["state"], "running")
        self.assertEqual(out["machine_id"], "M3")
        self.assertEqual(out["moves"][0]["from"], "M1")

    def test_hbv_patient_reseat_refuses_shared_machine(self):
        u = make_unit()
        u.register_patient("p3", dry_weight_kg=65, egfr=7.0, day=0)
        u.record_serology("p3", results={"hbv": "positive"}, day=0)
        u.start_session("S3", patient_id="p3", machine_id="M2",
                        dialyzer_id="D3", day=1)
        u.record_machine_fault("M2", day=1, description="leak",
                               by="rn-1")
        with self.assertRaises(NephroError) as ctx:
            u.reseat_session("S3", new_machine_id="M3", day=1,
                             by="rn-2")
        self.assertIn("serology", str(ctx.exception))

    def test_reseat_refuses_faulted_target_and_running_source(self):
        u = make_unit()
        u.register_patient("p2", dry_weight_kg=80, egfr=9.0, day=0)
        u.start_session("S1", patient_id="p1", machine_id="M1",
                        dialyzer_id="D1", day=1)
        u.record_machine_fault("M1", day=1, description="leak",
                               by="rn-1")
        u.record_machine_fault("M3", day=1, description="alarm",
                               by="rn-1")
        with self.assertRaises(NephroError):  # target faulted
            u.reseat_session("S1", new_machine_id="M3", day=1, by="rn-2")
        u.clear_machine_fault("M3", biomed_ref="BM-1", by="bio-1",
                              day=1)
        u.start_session("S9", patient_id="p2", machine_id="M2",
                        dialyzer_id="D9", day=1)
        with self.assertRaises(NephroError):  # source still running
            u.reseat_session("S9", new_machine_id="M3", day=1,
                             by="rn-2")

    def test_clearance_needs_biomed_ref_and_open_fault(self):
        u = make_unit()
        with self.assertRaises(NephroError):  # no open fault
            u.clear_machine_fault("M1", biomed_ref="BM-1", by="b",
                                  day=1)
        u.record_machine_fault("M1", day=1, description="leak",
                               by="rn-1")
        with self.assertRaises(NephroError):
            u.clear_machine_fault("M1", biomed_ref="", by="b", day=1)
        u.clear_machine_fault("M1", biomed_ref="BM-1", by="b", day=1)
        self.assertFalse(u.machines["M1"]["out_of_service"])
        self.assertEqual(u.faults_open(), [])


if __name__ == "__main__":
    unittest.main()
