
import unittest
from shiftshield import LimitRegistry, Rota, Shift, ShieldError


def _rota():
    reg = LimitRegistry()
    reg.ratify(max_weekly_hours=48, max_consecutive_nights=3,
               min_rest_hours=11, ratified_by="medical-director")
    return Rota(reg)


class TestShiftShield(unittest.TestCase):
    def test_unratified_limits_refuse_scheduling(self):
        reg = LimitRegistry()
        rota = Rota(reg)
        with self.assertRaises(ShieldError):
            rota.assign("dr-a", Shift(day=1, start_h=8, end_h=20))

    def test_normal_assignment_accepted(self):
        rota = _rota()
        rota.assign("dr-a", Shift(day=1, start_h=8, end_h=20))
        self.assertIn("dr-a", rota._shifts)

    def test_weekly_hours_limit_names_itself(self):
        rota = _rota()
        for d in range(1, 5):
            rota.assign("dr-a", Shift(day=d, start_h=8, end_h=20))  # 48h
        with self.assertRaises(ShieldError) as cm:
            rota.assign("dr-a", Shift(day=5, start_h=8, end_h=20))
        self.assertIn("48h week", str(cm.exception))

    def test_rest_limit_enforced(self):
        rota = _rota()
        rota.assign("dr-a", Shift(day=1, start_h=8, end_h=23))
        with self.assertRaises(ShieldError) as cm:
            rota.assign("dr-a", Shift(day=2, start_h=6, end_h=18))  # 7h rest
        self.assertIn("rest", str(cm.exception))

    def test_adequate_rest_passes(self):
        rota = _rota()
        rota.assign("dr-a", Shift(day=1, start_h=8, end_h=20))
        rota.assign("dr-a", Shift(day=2, start_h=8, end_h=20))  # 12h rest

    def test_consecutive_nights_capped(self):
        rota = _rota()
        for d in range(1, 4):
            rota.assign("dr-b", Shift(day=d, start_h=0, end_h=8, night=True))
        with self.assertRaises(ShieldError) as cm:
            rota.assign("dr-b", Shift(day=4, start_h=0, end_h=8, night=True))
        self.assertIn("night 4", str(cm.exception))

    def test_fatigue_score_advisory(self):
        rota = _rota()
        rota.assign("dr-a", Shift(day=1, start_h=8, end_h=20))
        # 12h rest after day-1 20h end — passes the 11h minimum
        rota.assign("dr-a", Shift(day=2, start_h=8, end_h=16, night=True))
        s = rota.fatigue_score("dr-a")
        self.assertGreater(s, 0)
        self.assertLessEqual(s, 100)

    def test_bad_shift_refused(self):
        with self.assertRaises(ShieldError):
            Shift(day=1, start_h=20, end_h=8)


if __name__ == "__main__":
    unittest.main()
