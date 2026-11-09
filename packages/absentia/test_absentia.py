"""test_absentia.py — absence claims with a stated search set."""

import pytest

from absentia import AbsentiaError, QueryEnvelope


def test_empty_search_refuses():
    env = QueryEnvelope("p1", as_of="2026-11-09")
    with pytest.raises(AbsentiaError, match="nothing was searched"):
        env.absence_claim("allergies")


def test_absence_enumerates_checked():
    env = QueryEnvelope("p1", as_of="2026-11-09")
    env.record_search("allergy-store", filters={"patient": "p1"},
                      findings=[])
    env.declare_not_checked("paper-records",
                            reason="legacy cabinet not digitised")
    claim = env.absence_claim("allergies")
    assert claim["absent_in"] == ["allergy-store"]
    assert claim["not_checked"][0]["store"] == "paper-records"
    assert "NOT CHECKED" in claim["verdict"]
    assert claim["fingerprint"]


def test_found_vs_absent_split():
    env = QueryEnvelope("p1", as_of="2026-11-09")
    env.record_search("allergy-store", findings=["penicillin"])
    env.record_search("medication-store", findings=[])
    claim = env.absence_claim("allergies")
    assert claim["found_in"] == ["allergy-store"]
    assert claim["absent_in"] == ["medication-store"]
    assert "present" not in claim["verdict"] or "found" in claim["verdict"]


def test_contracts():
    with pytest.raises(AbsentiaError, match="as-of"):
        QueryEnvelope("p1", as_of="")
    env = QueryEnvelope("p1", as_of="2026-11-09")
    with pytest.raises(AbsentiaError, match="reason"):
        env.declare_not_checked("x", reason="")
