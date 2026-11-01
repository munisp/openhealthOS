import os
import sys
import unittest

for pkg in ("teleread", "mdt"):
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", pkg))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from teleicu import TeleICU
from mdt import MDTError, TumourBoard


class TestTeleICU(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("icu-1", licence_id="I1", licence_expiry_day=400,
                   scopes=("intensivist",))
        self.svc = TeleICU(r)
        self.svc.open_oversight("IC1", patient_id="p1", facility="phc-1",
                                trigger_ref="trig-77", day=0,
                                severity="stat")
        self.svc.reading.assign("IC1", day=0, prefer="icu-1")

    def test_no_trigger_no_oversight(self):
        with self.assertRaises(TeleReadError):
            self.svc.open_oversight("IC2", patient_id="p2", facility="f",
                                    trigger_ref="", day=0)

    def test_execution_gap(self):
        self.svc.consult_note("IC1", intensivist="icu-1", day=0,
                              assessment="septic shock",
                              actions=["fluids-30mlkg", "cultures",
                                       "noradrenaline"])
        self.svc.confirm_action("IC1", action="fluids-30mlkg", day=0,
                                by="nurse-1")
        with self.assertRaises(TeleReadError):
            self.svc.confirm_action("IC1", action="dialysis", day=0,
                                    by="nurse-1")
        gap = self.svc.execution_gap("IC1")
        self.assertEqual(gap["confirmed"], 1)
        self.assertIn("cultures", gap["outstanding"])


class TestMDT(unittest.TestCase):
    def setUp(self):
        self.b = TumourBoard("state-cancer-board")
        for i, (mid, role, spec) in enumerate([
                ("m1", "chair", "oncology"), ("m2", "member", "radiology"),
                ("m3", "member", "pathology"), ("m4", "member", "surgery")]):
            self.b.add_member(mid, role=role, specialty=spec)
        self.b.submit_case("BC1", patient_id="p1", question="operable?",
                           evidence_refs=["RAD-R1", "PATH-R2"],
                           submitted_by="m2", day=0)

    def test_quorum_and_chair(self):
        with self.assertRaises(MDTError):
            self.b.hold_meeting("MTG1", day=1, chair="m2",
                                attendees=["m2", "m3", "m4"],
                                case_ids=["BC1"])
        with self.assertRaises(MDTError):
            self.b.hold_meeting("MTG1", day=1, chair="m1",
                                attendees=["m1", "m2"], case_ids=["BC1"])

    def test_decision_append_only(self):
        self.b.hold_meeting("MTG1", day=1, chair="m1",
                            attendees=["m1", "m2", "m3", "m4"],
                            case_ids=["BC1"])
        with self.assertRaises(MDTError):
            self.b.record_decision("MTG1", "BC1", day=1,
                                   decision="surgery then chemo",
                                   actions=[], recorder="m1")
        self.b.record_decision("MTG1", "BC1", day=1,
                               decision="surgery then chemo",
                               actions=["book-theatre", "baseline-bloods"],
                               recorder="m1")
        with self.assertRaises(MDTError):
            self.b.record_decision("MTG1", "BC1", day=2, decision="changed",
                                   actions=["x"], recorder="m1")
        st = self.b.execution_status("BC1", ["book-theatre"])
        self.assertEqual(st["outstanding"], ["baseline-bloods"])

    def test_no_evidence_no_case(self):
        with self.assertRaises(MDTError):
            self.b.submit_case("BC2", patient_id="p2", question="?",
                               evidence_refs=[], submitted_by="m2", day=0)


if __name__ == "__main__":
    unittest.main()
