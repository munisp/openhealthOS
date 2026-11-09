"""Dedicated suite for mdt.py — tumour-board doctrine under test."""
import unittest
from mdt import TumourBoard, MDTError


def _board():
    b = TumourBoard("LUTH oncology board")
    for i, role in enumerate(("chair", "surgeon", "oncologist", "pathologist")):
        b.add_member(f"m{i}", role=role, specialty=role)
    return b


class TestMDT(unittest.TestCase):
    def test_case_requires_evidence(self):
        b = _board()
        with self.assertRaises(MDTError):
            b.submit_case("c1", patient_id="p1", question="plan?",
                          evidence_refs=[], submitted_by="m0", day=1)

    def test_case_requires_question(self):
        b = _board()
        with self.assertRaises(MDTError):
            b.submit_case("c1", patient_id="p1", question="",
                          evidence_refs=["rad-1"], submitted_by="m0", day=1)

    def test_duplicate_case_refused(self):
        b = _board()
        b.submit_case("c1", patient_id="p1", question="plan?",
                      evidence_refs=["rad-1"], submitted_by="m0", day=1)
        with self.assertRaises(MDTError):
            b.submit_case("c1", patient_id="p2", question="again?",
                          evidence_refs=["rad-2"], submitted_by="m0", day=1)

    def test_unnamed_board_refused(self):
        with self.assertRaises(MDTError):
            TumourBoard("")


if __name__ == "__main__":
    unittest.main()
