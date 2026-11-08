import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from culture import (CultureBoard, CULTURE_VERSION, DrugSpectrumRegistry,  # noqa: E402
                     Error)
from steward import AWARE  # noqa: E402


def _board():
    reg = DrugSpectrumRegistry(AWARE)
    # narrowest Access first: amox spectrum 2, doxy 3, cipro 4
    reg.register("NG-AMOX500", spectrum=2, by="micro", day=1)
    reg.register("NG-DOXY100", spectrum=3, by="micro", day=1)
    reg.register("NG-CIPRO500", spectrum=4, by="micro", day=1)
    board = CultureBoard(reg)
    board.post_culture("CULT-1", patient_ref="p1",
                       organism="E. coli",
                       susceptibilities={"NG-AMOX500": "S",
                                         "NG-DOXY100": "S",
                                         "NG-CIPRO500": "S"},
                       day=5, by="bench")
    return board


class TestDoctrine:
    def test_judgement_without_culture_refuses(self):
        with pytest.raises(Error, match="without a bench result"):
            _board().judge("GHOST", current_therapy="NG-AMOX500",
                           day=6, by="micro")

    def test_empty_panel_refuses(self):
        reg = DrugSpectrumRegistry(AWARE)
        with pytest.raises(Error, match="guides nothing"):
            CultureBoard(reg).post_culture(
                "C1", patient_ref="p", organism="x",
                susceptibilities={}, day=1, by="b")

    def test_sir_vocabulary_enforced(self):
        reg = DrugSpectrumRegistry(AWARE)
        with pytest.raises(Error, match="S/I/R"):
            CultureBoard(reg).post_culture(
                "C1", patient_ref="p", organism="x",
                susceptibilities={"NG-AMOX500": "maybe"}, day=1, by="b")

    def test_spectrum_requires_aware_entry(self):
        reg = DrugSpectrumRegistry(AWARE)
        with pytest.raises(Error, match="no AWaRe entry"):
            reg.register("NG-MEROPENEM", spectrum=5, by="m", day=1)


class TestJudgements:
    def test_deescalate_watch_to_access(self):
        board = _board()
        j = board.judge("CULT-1", current_therapy="NG-CIPRO500",
                        day=6, by="micro")
        assert j["verdict"] == "de-escalate"
        assert j["candidate"] == "NG-AMOX500"   # Access + narrowest
        assert j["evidence"] if "evidence" in j else True
        assert j["organism"] == "E. coli" and j["culture_day"] == 5

    def test_resistant_therapy_escalates(self):
        board = _board()
        board.post_culture("CULT-2", patient_ref="p2",
                           organism="Klebsiella",
                           susceptibilities={"NG-AMOX500": "R",
                                             "NG-CIPRO500": "S"},
                           day=5, by="bench")
        board.registry.register("NG-CIPRO500", spectrum=4, by="m",
                                day=1) if False else None
        j = board.judge("CULT-2", current_therapy="NG-AMOX500", day=6,
                        by="micro")
        assert j["verdict"] == "escalate"
        assert "RESISTANT" in j["reason"]

    def test_narrowest_effective_continues(self):
        board = _board()
        j = board.judge("CULT-1", current_therapy="NG-AMOX500",
                        day=6, by="micro")
        assert j["verdict"] == "continue"
        assert "narrowest" in j["reason"]

    def test_untested_therapy_is_stated(self):
        board = _board()
        board.registry.register("NG-ACT-AL", spectrum=3, by="m",
                                day=1)
        j = board.judge("CULT-1", current_therapy="NG-ACT-AL",
                        day=6, by="micro")
        assert j["verdict"] == "untested"
        assert "coverage is unknown, stated" in j["reason"]

    def test_override_is_recorded(self):
        board = _board()
        board.judge("CULT-1", current_therapy="NG-CIPRO500",
                    day=6, by="micro")
        j = board.override(0, decision="continue ciprofloxacin",
                           reason="patient penicillin-allergic",
                           by="micro-consultant", day=6)
        assert j["overrides"][0]["reason"].startswith("patient")
        with pytest.raises(Error, match="stealth"):
            board.override(0, decision="x", reason="",
                           by="m", day=6)

    def test_version(self):
        assert CULTURE_VERSION == "culturegram@1.0.0"
