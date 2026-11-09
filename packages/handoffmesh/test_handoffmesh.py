
import unittest
from handoffmesh import HandoffBoard, HandoffError

SBAR_OK = {"situation": "BP 80/50 falling", "background": "day 2 post-op",
           "assessment": "possible sepsis", "recommendation": "review now"}


def _board():
    b = HandoffBoard()
    b.open("ho-1", patient_ref="p1", priority="critical",
           sender="nurse-night", receiver="nurse-day",
           sections=SBAR_OK, now_min=0)
    return b


class TestHandoff(unittest.TestCase):
    def test_open_requires_full_sbar(self):
        b = HandoffBoard()
        with self.assertRaises(HandoffError) as cm:
            b.open("ho-x", patient_ref="p1", priority="routine",
                   sender="a", receiver="b",
                   sections={"situation": "x"}, now_min=0)
        self.assertIn("background", str(cm.exception))

    def test_ack_by_wrong_person_refused(self):
        b = _board()
        with self.assertRaises(HandoffError):
            b.acknowledge("ho-1", by="random-passerby", now_min=5)

    def test_ack_closes_and_times(self):
        b = _board()
        mins = b.acknowledge("ho-1", by="nurse-day", now_min=10)
        self.assertEqual(mins, 10)
        self.assertEqual(b.open_for("nurse-day"), [])

    def test_silence_escalates_exactly_once(self):
        b = _board()
        first = b.sweep(20, duty_officer="mo-duty")   # > 15 min window
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["to"], "mo-duty")
        self.assertEqual(b.sweep(60, duty_officer="mo-duty"), [])

    def test_routine_window_longer(self):
        b = HandoffBoard()
        b.open("ho-2", patient_ref="p2", priority="routine",
               sender="a", receiver="b", sections=SBAR_OK, now_min=0)
        self.assertEqual(b.sweep(20, duty_officer="mo-duty"), [])
        self.assertEqual(len(b.sweep(300, duty_officer="mo-duty")), 1)

    def test_self_handoff_refused(self):
        b = HandoffBoard()
        with self.assertRaises(HandoffError):
            b.open("ho-x", patient_ref="p1", priority="routine",
                   sender="a", receiver="a", sections=SBAR_OK, now_min=0)

    def test_checksum_on_open(self):
        b = _board()
        self.assertEqual(len(b._cards["ho-1"]["checksum"]), 64)


if __name__ == "__main__":
    unittest.main()
