import os
import random
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from flowcast import (Error, FlowForecaster, FLOWCAST_VERSION)  # noqa: E402


def _history(n=56, seed=42, weekend_heavy=True):
    rng = random.Random(seed)
    out = []
    for d in range(n):
        base = 40.0
        if weekend_heavy and d % 7 in (5, 6):    # weekend surge
            base = 70.0
        out.append((d, max(0, int(base + rng.gauss(0, 4)))))
    return out


class TestDoctrine:
    def test_thin_history_refuses(self):
        with pytest.raises(Error, match="absence is not a value"):
            FlowForecaster().forecast([(i, 40) for i in range(10)])

    def test_no_history_refuses(self):
        with pytest.raises(Error, match="refuses to invent"):
            FlowForecaster().forecast([])

    def test_min_history_floor(self):
        with pytest.raises(Error, match="refused at construction"):
            FlowForecaster(min_history=3)

    def test_negative_admissions_refuse(self):
        h = _history()
        h[10] = (10, -5)
        with pytest.raises(Error, match="not data"):
            FlowForecaster().forecast(h)


class TestForecast:
    def test_weekend_structure_learned(self):
        fc = FlowForecaster().forecast(_history(), horizon_days=7,
                                       today_dow=4)   # Friday
        sat = next(d for d in fc["days"] if d["dow"] == 6)
        wed = next(d for d in fc["days"] if d["dow"] == 3)
        assert sat["p50"] > wed["p50"]

    def test_bands_wrap_the_point(self):
        fc = FlowForecaster().forecast(_history(), horizon_days=3)
        for d in fc["days"]:
            assert d["p10"] <= d["p50"] <= d["p90"]

    def test_deterministic(self):
        a = FlowForecaster().forecast(_history(), horizon_days=2)
        b = FlowForecaster().forecast(_history(), horizon_days=2)
        assert a == b

    def test_gaps_are_counted_not_interpolated(self):
        h = _history()
        h = [e for e in h if e[0] not in (10, 11, 12)]  # 3-day hole
        fc = FlowForecaster().forecast(h, horizon_days=1)
        assert fc["missing_days"] == 1    # one gap boundary
        assert "counted, not interpolated" in fc["degradation"]

    def test_band_label_follows_spread(self):
        calm = [(i, 40 + (i % 3)) for i in range(56)]
        fc = FlowForecaster().forecast(calm, horizon_days=1)
        assert fc["band"] == "narrow"

    def test_volatile_history_widens_band(self):
        rng = random.Random(7)
        wild = [(i, rng.choice([5, 90])) for i in range(56)]
        fc = FlowForecaster().forecast(wild, horizon_days=1)
        assert fc["band"] == "wide"


class TestStaffingGap:
    def test_shortfall_stated(self):
        fc = FlowForecaster().forecast(_history(), horizon_days=1,
                                       today_dow=5)
        gap = FlowForecaster().staffing_gap(
            fc["days"][0], minutes_per_patient=30,
            rostered_minutes=600)
        assert gap["state"].startswith("SHORTFALL")
        assert gap["gap_minutes"] < 0

    def test_covered_and_surge_state(self):
        fc = FlowForecaster().forecast(_history(), horizon_days=1,
                                       today_dow=2)
        gap = FlowForecaster().staffing_gap(
            fc["days"][0], minutes_per_patient=30,
            rostered_minutes=100_000)
        assert gap["state"] == "covered"
        assert gap["surge_state"] == "covered-at-p90"
        assert "named human" in gap["verdict"]

    def test_version(self):
        assert FLOWCAST_VERSION == "flowcast@1.0.0"
