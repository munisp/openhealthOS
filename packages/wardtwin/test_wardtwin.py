import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from wardtwin import Error, WardTwin, WARDTWIN_VERSION, what_if_close  # noqa: E402


ASSUMPTIONS = {"admissions_per_day": 6,
               "acuity_mix": {"low": 0.4, "medium": 0.4,
                              "high": 0.2},
               "nurse_hours_available_per_day": 96,
               "mean_turnover_hours": 3.0, "runs": 20, "seed": 42}


class TestDoctrine:
    def test_acuity_mix_must_sum_to_one(self):
        twin = WardTwin(name="ward-4", beds=30)
        bad = dict(ASSUMPTIONS, acuity_mix={"low": 0.9})
        with pytest.raises(Error, match="not 1.0"):
            twin.simulate(**bad)

    def test_no_acuity_mix_refuses(self):
        twin = WardTwin(name="ward-4", beds=30)
        with pytest.raises(Error, match="refuses to invent"):
            twin.simulate(**dict(ASSUMPTIONS, acuity_mix={}))

    def test_unknown_acuity_refuses(self):
        twin = WardTwin(name="ward-4", beds=30)
        with pytest.raises(Error, match="unknown acuity"):
            twin.simulate(**dict(ASSUMPTIONS,
                                 acuity_mix={"walking": 1.0}))

    def test_fewer_than_3_runs_is_anecdote(self):
        twin = WardTwin(name="ward-4", beds=30)
        with pytest.raises(Error, match="anecdote"):
            twin.simulate(**dict(ASSUMPTIONS, runs=2))

    def test_assumptions_echoed_verbatim(self):
        out = WardTwin(name="ward-4", beds=30).simulate(**ASSUMPTIONS)
        assert out["assumptions"]["acuity_mix"] == \
            ASSUMPTIONS["acuity_mix"]
        assert out["verdict"].endswith("named human")


class TestSimulation:
    def test_deterministic_same_seed(self):
        a = WardTwin(name="w", beds=30).simulate(**ASSUMPTIONS)
        b = WardTwin(name="w", beds=30).simulate(**ASSUMPTIONS)
        assert a["peak_queue"] == b["peak_queue"]

    def test_overloaded_ward_queues(self):
        heavy = dict(ASSUMPTIONS, admissions_per_day=20)
        out = WardTwin(name="w", beds=10).simulate(**heavy)
        assert out["peak_queue"]["p50"] > 0

    def test_confidence_is_computed_from_spread(self):
        out = WardTwin(name="w", beds=30).simulate(**ASSUMPTIONS)
        assert out["confidence"] in ("narrow", "moderate", "wide")
        assert "p10-p90 spread" in out["confidence_reason"]

    def test_understaffed_days_surface(self):
        thin = dict(ASSUMPTIONS, nurse_hours_available_per_day=10,
                    acuity_mix={"critical": 1.0})
        out = WardTwin(name="icu", beds=8).simulate(**thin)
        assert out["staffing_breach_days"]["p50"] > 0


class TestWhatIf:
    def test_close_and_divert_deltas_stated(self):
        ward_a = WardTwin(name="ward-4", beds=20)
        ward_b = WardTwin(name="ward-5", beds=30)
        out = what_if_close(ward_a, ward_b,
                            closing_assumptions=ASSUMPTIONS,
                            receiving_assumptions=dict(ASSUMPTIONS))
        assert "close ward-4" in out["scenario"]
        assert "delta_peak_queue_p50" in out
        assert out["after"]["assumptions"]["admissions_per_day"] == 12

    def test_version(self):
        assert WARDTWIN_VERSION == "wardtwin@1.0.0"
