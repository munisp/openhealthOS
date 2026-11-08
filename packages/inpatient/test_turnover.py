import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from inpatient import Ward  # noqa: E402
from turnover import Error, TurnoverBoard, TURNOVER_VERSION  # noqa: E402


def _board():
    ward = Ward("NG-LUTH")
    ward.add_bed("B1", "ward-3", kind="general")
    ep = ward.admit("pat-1", "B1", actor="nurse-a")
    ward.discharge(ep.id, actor="nurse-a")   # vacated -> cleaning
    board = TurnoverBoard(ward)
    board.declare_sla("ward-3", minutes=60, by="nurse-mgr", day=1)
    return board


def _done_job(board):
    board.open_job("B1", at_minute=100, by="nurse-a", day=1)
    board.assign_crew("B1", crew="hk-crew-1", eta_minutes=30,
                      by="nurse-a", day=1)
    board.cleaning_done("B1", at_minute=125, by="hk-crew-1", day=1)


class TestOpenJob:
    def test_opens_only_on_vacated_bed(self):
        board = _board()
        board.ward.set_bed_state("B1", "available")   # ready, not dirty
        with pytest.raises(Error, match="not cleaning"):
            board.open_job("B1", at_minute=0, by="n", day=1)

    def test_no_sla_no_job(self):
        ward = Ward("NG-LUTH")
        ward.add_bed("B9", "ward-x")
        ep = ward.admit("pat-9", "B9", actor="n")
        ward.discharge(ep.id, actor="n")
        board = TurnoverBoard(ward)
        with pytest.raises(Error, match="declare the clock"):
            board.open_job("B9", at_minute=0, by="n", day=1)

    def test_double_open_refuses(self):
        board = _board()
        board.open_job("B1", at_minute=0, by="n", day=1)
        with pytest.raises(Error, match="already open"):
            board.open_job("B1", at_minute=1, by="n", day=1)


class TestLifecycle:
    def test_full_turnover(self):
        board = _board()
        _done_job(board)
        job = board.release("B1", inspector="nurse-in-charge",
                            at_minute=130, day=1)
        assert job["turnover_minutes"] == 30
        assert board.ward.beds["B1"].state == "available"
        assert "B1" not in board.jobs

    def test_crew_never_inspects(self):
        board = _board()
        _done_job(board)
        with pytest.raises(Error, match="never inspects"):
            board.release("B1", inspector="hk-crew-1", at_minute=130,
                          day=1)

    def test_only_assigned_crew_reports_done(self):
        board = _board()
        board.open_job("B1", at_minute=100, by="n", day=1)
        board.assign_crew("B1", crew="hk-crew-1", eta_minutes=30,
                          by="n", day=1)
        with pytest.raises(Error, match="its own done"):
            board.cleaning_done("B1", at_minute=120, by="random",
                                day=1)

    def test_terminal_clean_signed_by_name(self):
        board = _board()
        board.open_job("B1", terminal=True, at_minute=100, by="n",
                       day=1)
        board.assign_crew("B1", crew="hk-crew-1", eta_minutes=45,
                          by="n", day=1)
        board.cleaning_done("B1", at_minute=140, by="hk-crew-1", day=1)
        with pytest.raises(Error, match="terminal"):
            board.release("B1", inspector="nurse-in-charge",
                          at_minute=145, day=1)
        job = board.release("B1", inspector="nurse-in-charge",
                            at_minute=145, day=1,
                            terminal_signed_by="nurse-in-charge")
        assert job["terminal"] is True


class TestSlaAndKpis:
    def test_overdue_is_surfaced(self):
        board = _board()
        board.open_job("B1", at_minute=100, by="n", day=1)
        overdue = board.overdue(at_minute=200)
        assert len(overdue) == 1
        assert overdue[0]["age_minutes"] == 100
        assert "60 min SLA" in overdue[0]["reason"]

    def test_kpis_from_completed(self):
        board = _board()
        _done_job(board)
        board.release("B1", inspector="nic", at_minute=130, day=1)
        k = board.kpis(ward="ward-3")
        assert k["completed"] == 1 and k["median_minutes"] == 30
        assert k["sla_breaches"] == 0

    def test_empty_kpis_are_stated(self):
        board = _board()
        k = board.kpis(ward="ward-3")
        assert k["median_minutes"] is None
        assert "stated, not zeroed" in k["reason"]

    def test_version(self):
        assert TURNOVER_VERSION == "bedturnover@1.0.0"
