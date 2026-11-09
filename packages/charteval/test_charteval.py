"""test_charteval.py — scoring harness for clinical-LLM features."""

import pytest

from charteval import (ChartEvalError, assign_split, finding_f1,
                       floor_verdict, ndcg_at_k, precision_at_k,
                       report, severity_weighted_f1)


def test_swf1_perfect():
    ref = {"sepsis": 3, "t2dm": 1}
    assert severity_weighted_f1(ref, ["sepsis", "t2dm"]) == 1.0


def test_swf1_severity_weights():
    ref = {"sepsis": 3, "acne": 1}
    # missing the severe item hurts more than missing the mild one
    miss_severe = severity_weighted_f1(ref, ["acne"])
    miss_mild = severity_weighted_f1(ref, ["sepsis"])
    assert miss_severe < miss_mild


def test_swf1_chart_neutral():
    ref = {"sepsis": 3, "t2dm": 1}
    chart = {"sepsis", "t2dm", "hypertension"}  # charted but unreferenced
    neutral = severity_weighted_f1(ref, ["sepsis", "t2dm", "hypertension"],
                                   chart=chart, chart_neutral=True)
    assert neutral == 1.0  # neither credited nor penalised
    strict = severity_weighted_f1(ref, ["sepsis", "t2dm", "hypertension"],
                                  chart=chart, chart_neutral=False)
    assert strict < 1.0  # extra counts against
    # hallucination (in neither ref nor chart) always counts
    hallu = severity_weighted_f1(ref, ["sepsis", "t2dm", "lupus"],
                                 chart=chart, chart_neutral=True)
    assert hallu < 1.0


def test_swf1_empty_reference_refuses():
    with pytest.raises(ChartEvalError, match="empty reference"):
        severity_weighted_f1({}, ["x"])


def test_finding_f1():
    assert finding_f1({"a", "b"}, {"a", "b"}) == 1.0
    assert finding_f1({"a", "b"}, {"a"}) == pytest.approx(2 / 3)
    assert finding_f1({"a"}, set()) == 0.0
    with pytest.raises(ChartEvalError):
        finding_f1(set(), {"a"})


def test_precision_and_ndcg():
    ranked = ["d1", "d2", "d3", "d4", "d5"]
    assert precision_at_k(ranked, {"d1", "d3"}, k=5) == 0.4
    graded = {"d1": 3, "d2": 0, "d3": 2, "d4": 1, "d5": 0}
    assert ndcg_at_k(["d1", "d3", "d4"], graded, k=3) == pytest.approx(1.0)
    with pytest.raises(ChartEvalError, match="undefined"):
        ndcg_at_k(ranked, {"x": 0}, k=5)


def test_splits_patient_level_deterministic():
    patients = [f"p{i}" for i in range(200)]
    a = {p: assign_split(p, salt="v1") for p in patients}
    b = {p: assign_split(p, salt="v1") for p in patients}
    assert a == b  # deterministic
    assert set(a.values()) <= {"train", "public", "heldout"}
    # salt changes the draw (a new benchmark version is a new split)
    c = {p: assign_split(p, salt="v2") for p in patients}
    assert sum(1 for p in patients if a[p] != c[p]) > 0


def test_split_weights_must_sum():
    with pytest.raises(ChartEvalError, match="sum to 1"):
        assign_split("p1", splits=(("train", 0.5),))


def test_floor_verdict_and_report():
    below = floor_verdict(0.60, physician_mean=0.73, n=100)
    assert "conversation" in below["verdict"]
    above = floor_verdict(0.80, physician_mean=0.73, n=100)
    assert "above" in above["verdict"]
    with pytest.raises(ChartEvalError, match="marketing"):
        floor_verdict(0.9, physician_mean=0.73, n=0)
    r = report("patient-diagnosis", [0.5, 0.7, 0.9])
    assert r == {"task": "patient-diagnosis", "n": 3, "mean": 0.7,
                 "min": 0.5, "max": 0.9}
