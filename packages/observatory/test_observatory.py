"""Tests for observatory — population-health observatory."""

import pytest

from observatory import (Error, IndicatorRegistry, Observatory, K_FLOOR,
                         VERSION)


@pytest.fixture
def obs():
    o = Observatory()
    o.register_region("CV", "Cabo Verde")
    o.register_region("CV-ST", "Santiago", parent="CV")
    o.register_region("CV-PRAIA", "Praia", parent="CV-ST")
    o.registry.propose("malaria-incidence", {
        "event_kind": "case", "numerator": ["B50", "B54"],
        "title": "Malaria cases", "version": "mi@1"}, by="epi-1", day=1)
    o.registry.ratify("malaria-incidence", by="epi-2", day=1)
    return o


class TestRegistry:
    def test_four_eyes_ratification(self):
        r = IndicatorRegistry()
        r.propose("x", {"event_kind": "case", "numerator": ["A00"],
                        "title": "t"}, by="a", day=1)
        with pytest.raises(Error, match="proposes never approves"):
            r.ratify("x", by="a", day=1)
        r.ratify("x", by="b", day=1)
        assert r.live("x")["status"] == "live"

    def test_unratified_indicator_refuses_compute(self):
        o = Observatory()
        o.register_region("R", "R")
        o.registry.propose("x", {"event_kind": "case",
                                 "numerator": ["A00"], "title": "t"},
                           by="a", day=1)
        with pytest.raises(Error, match="no live indicator"):
            o.compute("x", region="R", period_start=1, period_end=10)

    def test_definition_requires_fields(self):
        r = IndicatorRegistry()
        with pytest.raises(Error, match="missing"):
            r.propose("x", {"event_kind": "case"}, by="a", day=1)


class TestGeography:
    def test_unknown_region_refuses(self, obs):
        with pytest.raises(Error, match="unknown region"):
            obs.record_event("case", region="XX", day=1,
                             condition="B54", source="HAN")

    def test_rollup_counts_children(self, obs):
        obs.record_event("case", region="CV-PRAIA", day=5,
                         condition="B54", count=7, source="HAN")
        obs.record_event("case", region="CV-ST", day=6,
                         condition="B50", count=6, source="delegacia")
        at_country = obs.compute("malaria-incidence", region="CV",
                                 period_start=1, period_end=10)
        assert at_country["value"] == 13  # 7 in Praia + 6 in Santiago
        at_praia = obs.compute("malaria-incidence", region="CV-PRAIA",
                               period_start=1, period_end=10)
        assert at_praia["value"] == 7


class TestCompute:
    def test_k_floor_suppresses_with_statement(self, obs):
        obs.record_event("case", region="CV-PRAIA", day=5,
                         condition="B54", count=K_FLOOR - 1,
                         source="HAN")
        out = obs.compute("malaria-incidence", region="CV-PRAIA",
                          period_start=1, period_end=10)
        assert out["value"] is None
        assert out["suppressed"] is True
        assert "k-floor" in out["suppression"]

    def test_zero_is_stated_not_suppressed(self, obs):
        out = obs.compute("malaria-incidence", region="CV",
                          period_start=1, period_end=10)
        assert out["value"] == 0
        assert out["suppressed"] is False
        assert "zero events" in out["note"]

    def test_period_filter(self, obs):
        obs.record_event("case", region="CV", day=3, condition="B54",
                         count=10, source="x")
        obs.record_event("case", region="CV", day=30,
                         condition="B54", count=10, source="x")
        out = obs.compute("malaria-incidence", region="CV",
                          period_start=1, period_end=10)
        assert out["value"] == 10

    def test_numerator_filter(self, obs):
        obs.record_event("case", region="CV", day=3, condition="B54",
                         count=10, source="x")
        obs.record_event("case", region="CV", day=3, condition="J06",
                         count=99, source="x")
        out = obs.compute("malaria-incidence", region="CV",
                          period_start=1, period_end=10)
        assert out["value"] == 10

    def test_event_requires_source_and_count(self, obs):
        with pytest.raises(Error, match="source"):
            obs.record_event("case", region="CV", day=1,
                             condition="B54", source="")
        with pytest.raises(Error, match="positive"):
            obs.record_event("case", region="CV", day=1,
                             condition="B54", count=0, source="x")


class TestSeriesAndPublish:
    def test_series_carries_provenance_per_point(self, obs):
        for d in range(1, 8):
            obs.record_event("case", region="CV", day=d,
                             condition="B54", count=5, source="x")
        s = obs.series("malaria-incidence", region="CV",
                       period_start=1, period_end=6, step=3)
        assert [p["value"] for p in s] == [15, 15]
        assert all(len(p["events_sha256"]) == 64 for p in s)
        assert all(p["version"] == VERSION for p in s)

    def test_publish_is_deterministic(self, obs):
        obs.record_event("case", region="CV", day=3, condition="B54",
                         count=10, source="x")
        a = obs.publish("malaria-incidence", region="CV",
                        period_start=1, period_end=10, by="epi-1", day=9)
        b = obs.publish("malaria-incidence", region="CV",
                        period_start=1, period_end=10, by="epi-1", day=9)
        assert a["publication_sha256"] == b["publication_sha256"]
        assert a["published_by"] == "epi-1"

    def test_publish_names_publisher(self, obs):
        with pytest.raises(Error, match="publisher"):
            obs.publish("malaria-incidence", region="CV",
                        period_start=1, period_end=10, by="", day=9)
