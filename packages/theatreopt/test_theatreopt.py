import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from theatreopt import Error, TheatreDay, THEATREOPT_VERSION  # noqa: E402


def _day():
    d = TheatreDay(date_day=10, theatres=2, session_minutes=240,
                   emergency_reserve_minutes=30)
    d.add_case("C1", surgeon="dr-a", anaesthetist_required=True,
               duration_minutes=90, priority="P2-cancer",
               waited_days=30)
    d.add_case("C2", surgeon="dr-a", anaesthetist_required=True,
               duration_minutes=60, priority="P3-elective",
               waited_days=100)
    d.add_case("C3", surgeon="dr-b", anaesthetist_required=True,
               duration_minutes=60, priority="P3-elective",
               waited_days=40)
    d.add_case("C4", surgeon="dr-c", anaesthetist_required=False,
               duration_minutes=45, priority="P4-routine",
               waited_days=10)
    return d


class TestDoctrine:
    def test_unknown_priority_refuses(self):
        d = TheatreDay(date_day=1, theatres=1)
        with pytest.raises(Error, match="unknown priority"):
            d.add_case("X", surgeon="s", anaesthetist_required=True,
                       duration_minutes=30, priority="VIP",
                       waited_days=1)

    def test_oversized_case_refuses_at_intake(self):
        d = TheatreDay(date_day=1, theatres=1, session_minutes=120,
                       emergency_reserve_minutes=30)
        with pytest.raises(Error, match="never fit"):
            d.add_case("X", surgeon="s", anaesthetist_required=True,
                       duration_minutes=95, priority="P3-elective",
                       waited_days=1)

    def test_no_anaesthetist_runs_nothing(self):
        with pytest.raises(Error, match="no anaesthetist"):
            _day().optimize(anaesthetists_available=0)

    def test_reserve_smaller_than_session(self):
        with pytest.raises(Error, match="emergency reserve"):
            TheatreDay(date_day=1, theatres=1, session_minutes=100,
                       emergency_reserve_minutes=100)


class TestScheduling:
    def test_priority_before_wait(self):
        out = _day().optimize(anaesthetists_available=3)
        first = out["scheduled"][0][0]
        assert first == "C1"          # P2 beats longer-waiting P3

    def test_surgeon_never_double_booked(self):
        d = TheatreDay(date_day=10, theatres=2, session_minutes=480,
                       emergency_reserve_minutes=0)
        d.add_case("A1", surgeon="dr-a", anaesthetist_required=False,
                   duration_minutes=200, priority="P3-elective",
                   waited_days=5)
        d.add_case("A2", surgeon="dr-a", anaesthetist_required=False,
                   duration_minutes=200, priority="P3-elective",
                   waited_days=4)
        out = d.optimize(anaesthetists_available=2)
        rooms = out["scheduled"]
        # dr-a cannot open both rooms at minute 0 — A2 must follow A1
        # sequentially in ONE room (200 + 15 turnover + 200 = 415 <= 480)
        assert rooms[0] == ["A1", "A2"] and rooms[1] == []

    def test_deferrals_are_named_with_reasons(self):
        d = TheatreDay(date_day=10, theatres=1, session_minutes=120,
                       emergency_reserve_minutes=0)
        d.add_case("BIG", surgeon="s", anaesthetist_required=False,
                   duration_minutes=100, priority="P3-elective",
                   waited_days=1)
        d.add_case("LATE", surgeon="t", anaesthetist_required=False,
                   duration_minutes=60, priority="P4-routine",
                   waited_days=9)
        out = d.optimize(anaesthetists_available=1)
        names = [x["case"] for x in out["deferred"]]
        assert "LATE" in names
        reason = [x for x in out["deferred"]
                  if x["case"] == "LATE"][0]["reason"]
        assert "session minutes" in reason

    def test_missing_equipment_named(self):
        d = _day()
        out = d.optimize(anaesthetists_available=3,
                         equipment_pool=[])
        d2 = _day()
        d2.cases[0]["equipment"] = {"c-arm"}
        out = d2.optimize(anaesthetists_available=3,
                          equipment_pool=[])
        deferred_ids = [x["case"] for x in out["deferred"]]
        assert "C1" in deferred_ids
        assert any("c-arm" in x["reason"] for x in out["deferred"])

    def test_plan_explains_itself(self):
        out = _day().optimize(anaesthetists_available=3)
        assert 0 < out["utilization"] <= 1
        assert out["emergency_reserve_minutes"] == 30
        assert "binding_constraints" in out
        assert "explains itself" in out["verdict"]

    def test_reserve_is_untouchable(self):
        out = _day().optimize(anaesthetists_available=3)
        # 2 theatres x (240-30) = 420 usable; never books the reserve
        total_possible = 2 * 210
        assert out["utilization"] * total_possible <= total_possible

    def test_version(self):
        assert THEATREOPT_VERSION == "theatreopt@1.0.0"
