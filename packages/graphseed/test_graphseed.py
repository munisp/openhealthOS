"""test_graphseed.py — graph-first synthetic patient generation."""

import pytest

from graphseed import (CaseLibrary, ClinicalGraph, GraphSeedError,
                       assemble_patients, nigeria_starter_library,
                       render_patient)


@pytest.fixture()
def lib():
    return nigeria_starter_library()


@pytest.fixture()
def graph(lib):
    return ClinicalGraph().load_library(lib)


def test_provenance_required(lib):
    with pytest.raises(GraphSeedError, match="without an author"):
        lib.add_case("x", source_ref="", age=30, sex="M",
                     diagnoses=[{"code": "B54", "role": "primary",
                                 "acuity": 2, "label": "malaria"}],
                     findings=[])
    with pytest.raises(GraphSeedError, match="bad ICD-10"):
        lib.add_case("y", source_ref="s", age=30, sex="M",
                     diagnoses=[{"code": "not-a-code", "role": "primary",
                                 "acuity": 1, "label": "x"}],
                     findings=[])


def test_graph_provenance_and_grounding(graph):
    dx = graph.nodes["dx:B54"]
    assert dx["sources"]
    assert "SNOMED absent" in dx["grounding"]  # honest about licensing
    assert any(e["type"] == "manifests" and e["from"] == "dx:B54"
               for e in graph.edges)
    assert graph.fingerprint() == graph.fingerprint()  # stable


def test_shared_diagnosis_clusters(graph, lib):
    # t2dm-1, hhs-1 both hold dx:E11.9 -> must cluster together
    case_ids = [c["case_id"] for c in lib.cases()]
    clusters = assemble_patients(graph, case_ids, n_patients=20, seed=42)
    flat = [c for cl in clusters for c in cl]
    assert len(flat) == len(set(flat))  # each case used at most once
    for cl in clusters:
        if "case-t2dm-1" in cl or "case-hhs-1" in cl:
            assert "case-t2dm-1" in cl and "case-hhs-1" in cl


def test_render_deterministic_and_labelled(graph, lib):
    case_ids = [c["case_id"] for c in lib.cases()]
    clusters = assemble_patients(graph, case_ids, n_patients=20, seed=42)
    target = next(cl for cl in clusters if "case-hhs-1" in cl)
    a = render_patient(graph, lib, target, patient_ref="p1", seed=42)
    b = render_patient(graph, lib, target, patient_ref="p1", seed=42)
    assert a["chart_fingerprint"] == b["chart_fingerprint"]
    # labels are graph-derived and fingerprinted
    assert a["label_fingerprint"]
    codes = {p["code"] for p in a["labels"]["problem_list"]}
    assert "E11.0" in codes and "E11.9" in codes
    # chronic problems carry forward across encounters
    last = a["encounters"][-1]
    assert "E11.9" in last["problem_list"]
    # provenance tags travel
    assert a["meta"]["tag"] == "synthetic"
    assert a["graph_fingerprint"] == graph.fingerprint()


def test_incompatible_cluster_refused(graph, lib):
    with pytest.raises(GraphSeedError, match="age spread"):
        render_patient(graph, lib, ["case-malaria-1", "case-hhs-1"],
                       patient_ref="px", seed=1)
    with pytest.raises(GraphSeedError, match="sex mismatch"):
        render_patient(graph, lib, ["case-t2dm-1", "case-htn-1"],
                       patient_ref="py", seed=1)


def test_starter_library_case_mix(lib):
    codes = {d["code"] for c in lib.cases() for d in c["diagnoses"]}
    # the ADR-0045 gap: infectious + maternal + haemoglobinopathic
    assert {"B54", "A15.0", "B20", "D57.1", "A01.0", "O14.1"} <= codes
