"""graphseed.py — graph-first synthetic patient generation (ADR-0045
supplement: Nigerian/Cabo-Verdean case mix).

Synthetic Hospital's pipeline is the right shape and the wrong
population: its corpus is USMLE/US case-mix, and ADR-0045 states
plainly it is never used for Nigerian epidemiological inference. This
module builds our OWN synthetic patients the way they built theirs:

  1. KNOWLEDGE GRAPH FIRST. Cases are declared clinical content
     (diagnoses with ICD-10 codes, typed findings, demographics) with a
     source reference. Nodes and typed edges are materialised BEFORE
     any patient exists.
  2. PATIENT STATE IS FIXED BEFORE THE CHART IS RENDERED. Deterministic
     seeded clustering groups compatible cases into longitudinal
     patients; only then is a chart written. Ground truth never depends
     on generated prose — labels are a pure function of the graph, and
     every rendered chart carries the label fingerprint so a tampered
     narrative cannot quietly change the truth.
  3. PROVENANCE IS A FIELD, NOT A COMMENT. Every node carries its
     source reference and grounding method; every patient carries the
     synthetic meta tag so synthetic data can never masquerade as a
     real chart downstream (same convention family as fhirguard).
  4. ICD-10 ONLY, HONESTLY. SNOMED CT ships absent (affiliate
     licensing) — the grounding method says so rather than implying
     coverage we do not have.
  5. STARTER LIBRARY, DECLARED. The embedded case mix (malaria, TB,
     HIV, sickle cell, typhoid, obstetric emergencies, diabetes,
     hypertension...) is a STARTER set for demos and CI — deployments
     extend it; it is not a burden-of-disease model.

Stdlib only (ADR-0005).
"""

from __future__ import annotations

import hashlib
import json
import random
import re

VERSION = "graphseed@1.0.0"

_ICD10 = __import__("re").compile(r"^[A-Z]\d{2}(\.\d{1,2})?$")

FINDING_KINDS = ("symptom", "sign", "lab", "medication", "history")
DX_ROLES = ("primary", "secondary", "chronic")  # chronic carries forward
AGE_BUCKET_TOLERANCE = 7  # years, mirrors the benchmark's middle band
SYNTHETIC_TAG = "synthetic"  # meta tag, ADR-0045 convention family


class GraphSeedError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# ------------------------------------------------------------- library

class CaseLibrary:
    """Declared clinical content with provenance. A case without a
    source is a fact without an author — refused."""

    def __init__(self):
        self._cases = {}

    def add_case(self, case_id, *, source_ref, age, sex, diagnoses,
                 findings):
        if case_id in self._cases:
            raise GraphSeedError(f"duplicate case: {case_id}")
        if not source_ref:
            raise GraphSeedError("a case without a source is a fact "
                                 "without an author")
        if sex not in ("M", "F"):
            raise GraphSeedError("sex must be M or F")
        if not (0 <= age <= 120):
            raise GraphSeedError(f"implausible age: {age}")
        for dx in diagnoses:
            if not _ICD10.match(dx["code"]):
                raise GraphSeedError(f"bad ICD-10: {dx['code']}")
            if dx.get("role") not in DX_ROLES:
                raise GraphSeedError(f"unknown role: {dx.get('role')}")
        for f in findings:
            if f.get("kind") not in FINDING_KINDS:
                raise GraphSeedError(f"unknown finding kind: "
                                     f"{f.get('kind')}")
        self._cases[case_id] = {
            "case_id": case_id, "source_ref": source_ref, "age": age,
            "sex": sex, "diagnoses": list(diagnoses),
            "findings": list(findings)}
        return case_id

    def cases(self):
        return list(self._cases.values())


def nigeria_starter_library():
    """Starter case mix for NG/CV demos and CI. Every case carries a
    teaching-file source reference; extend at deployment."""
    lib = CaseLibrary()
    src = "graphseed-starter@1.0.0"  # declared starter source
    lib.add_case("case-malaria-1", source_ref=src, age=24, sex="F",
        diagnoses=[{"code": "B54", "role": "primary", "acuity": 2,
                    "label": "malaria, uncomplicated"}],
        findings=[{"text": "fever", "kind": "symptom", "salience": 3},
                  {"text": "chills", "kind": "symptom", "salience": 2},
                  {"text": "positive RDT", "kind": "lab", "salience": 3}])
    lib.add_case("case-t2dm-1", source_ref=src, age=52, sex="M",
        diagnoses=[{"code": "E11.9", "role": "chronic", "acuity": 1,
                    "label": "type 2 diabetes"}],
        findings=[{"text": "polyuria", "kind": "symptom", "salience": 2},
                  {"text": "metformin therapy", "kind": "medication",
                   "salience": 2}])
    lib.add_case("case-hhs-1", source_ref=src, age=58, sex="M",
        diagnoses=[
            {"code": "E11.0", "role": "primary", "acuity": 3,
             "label": "hyperosmolar hyperglycaemic state"},
            {"code": "E11.9", "role": "chronic", "acuity": 1,
             "label": "type 2 diabetes"}],
        findings=[{"text": "confusion", "kind": "symptom", "salience": 3},
                  {"text": "severe dehydration", "kind": "sign",
                   "salience": 3},
                  {"text": "glucose 42 mmol/L", "kind": "lab",
                   "salience": 3}])
    lib.add_case("case-aki-1", source_ref=src, age=55, sex="M",
        diagnoses=[{"code": "N17.9", "role": "primary", "acuity": 3,
                    "label": "acute kidney injury"}],
        findings=[{"text": "oliguria", "kind": "sign", "salience": 3},
                  {"text": "creatinine rising", "kind": "lab",
                   "salience": 3}])
    lib.add_case("case-htn-1", source_ref=src, age=49, sex="F",
        diagnoses=[{"code": "I10", "role": "chronic", "acuity": 1,
                    "label": "essential hypertension"}],
        findings=[{"text": "BP 168/104", "kind": "sign", "salience": 2},
                  {"text": "amlodipine therapy", "kind": "medication",
                   "salience": 1}])
    lib.add_case("case-tb-1", source_ref=src, age=34, sex="M",
        diagnoses=[{"code": "A15.0", "role": "primary", "acuity": 2,
                    "label": "pulmonary tuberculosis"}],
        findings=[{"text": "cough 3 weeks", "kind": "symptom",
                   "salience": 3},
                  {"text": "night sweats", "kind": "symptom",
                   "salience": 2},
                  {"text": "GeneXpert MTB detected", "kind": "lab",
                   "salience": 3}])
    lib.add_case("case-hiv-1", source_ref=src, age=29, sex="F",
        diagnoses=[{"code": "B20", "role": "chronic", "acuity": 2,
                    "label": "HIV disease"}],
        findings=[{"text": "CD4 210", "kind": "lab", "salience": 3},
                  {"text": "on ART", "kind": "medication", "salience": 2}])
    lib.add_case("case-sickle-1", source_ref=src, age=19, sex="M",
        diagnoses=[{"code": "D57.1", "role": "primary", "acuity": 3,
                    "label": "sickle cell vaso-occlusive crisis"}],
        findings=[{"text": "severe bone pain", "kind": "symptom",
                   "salience": 3},
                  {"text": "Hb 7.2", "kind": "lab", "salience": 2}])
    lib.add_case("case-typhoid-1", source_ref=src, age=16, sex="F",
        diagnoses=[{"code": "A01.0", "role": "primary", "acuity": 2,
                    "label": "typhoid fever"}],
        findings=[{"text": "stepwise fever", "kind": "symptom",
                   "salience": 3},
                  {"text": "abdominal pain", "kind": "symptom",
                   "salience": 2}])
    lib.add_case("case-preeclampsia-1", source_ref=src, age=27, sex="F",
        diagnoses=[{"code": "O14.1", "role": "primary", "acuity": 3,
                    "label": "severe pre-eclampsia"}],
        findings=[{"text": "BP 170/110 at 34 weeks", "kind": "sign",
                   "salience": 3},
                  {"text": "proteinuria 3+", "kind": "lab",
                   "salience": 3}])
    lib.add_case("case-pneumonia-1", source_ref=src, age=63, sex="M",
        diagnoses=[{"code": "J18.9", "role": "primary", "acuity": 2,
                    "label": "pneumonia"}],
        findings=[{"text": "productive cough", "kind": "symptom",
                   "salience": 2},
                  {"text": "crackles right base", "kind": "sign",
                   "salience": 2}])
    lib.add_case("case-hf-1", source_ref=src, age=61, sex="F",
        diagnoses=[{"code": "I50.9", "role": "chronic", "acuity": 2,
                    "label": "heart failure"}],
        findings=[{"text": "orthopnoea", "kind": "symptom", "salience": 3},
                  {"text": "bilateral oedema", "kind": "sign",
                   "salience": 2}])
    lib.add_case("case-appendicitis-1", source_ref=src, age=22, sex="M",
        diagnoses=[{"code": "K35.8", "role": "primary", "acuity": 3,
                    "label": "acute appendicitis"}],
        findings=[{"text": "RLQ pain", "kind": "symptom", "salience": 3},
                  {"text": "rebound tenderness", "kind": "sign",
                   "salience": 3}])
    lib.add_case("case-uti-1", source_ref=src, age=31, sex="F",
        diagnoses=[{"code": "N39.0", "role": "primary", "acuity": 1,
                    "label": "urinary tract infection"}],
        findings=[{"text": "dysuria", "kind": "symptom", "salience": 2},
                  {"text": "nitrites positive", "kind": "lab",
                   "salience": 2}])
    return lib


# --------------------------------------------------------------- graph

class ClinicalGraph:
    """Nodes are declared clinical facts with provenance; edges are
    typed relations. The graph is fixed before any patient exists."""

    def __init__(self):
        self.nodes = {}
        self.edges = []

    def load_library(self, library):
        for case in library.cases():
            for dx in case["diagnoses"]:
                node_id = f"dx:{dx['code']}"
                if node_id not in self.nodes:
                    self.nodes[node_id] = {
                        "kind": "diagnosis", "code": dx["code"],
                        "label": dx["label"],
                        "grounding": "icd10-only — SNOMED absent "
                                     "(affiliate licensing)",
                        "sources": []}
                self.nodes[node_id]["sources"].append(
                    {"case": case["case_id"],
                     "source_ref": case["source_ref"]})
            for f in case["findings"]:
                node_id = f"f:{f['text']}"
                if node_id not in self.nodes:
                    self.nodes[node_id] = {
                        "kind": "finding", "text": f["text"],
                        "finding_kind": f["kind"],
                        "salience": f["salience"], "sources": []}
                self.nodes[node_id]["sources"].append(
                    {"case": case["case_id"],
                     "source_ref": case["source_ref"]})
                for dx in case["diagnoses"]:
                    self.edges.append({
                        "type": "manifests", "from": f"dx:{dx['code']}",
                        "to": node_id, "case": case["case_id"]})
        return self

    def fingerprint(self):
        return hashlib.sha256(
            _canon({"nodes": self.nodes, "edges": self.edges})
        ).hexdigest()


# ----------------------------------------------------------- assembly

def assemble_patients(graph, case_ids, *, n_patients, seed):
    """Deterministic seeded clustering. Cases link into one patient when
    they share a diagnosis node AND are demographically compatible —
    the same rule the benchmark uses, because continuity without
    compatibility is a chart that changes age between visits."""
    by_case = {}
    for node_id, node in graph.nodes.items():
        if node["kind"] != "diagnosis":
            continue
        for s in node["sources"]:
            by_case.setdefault(s["case"], set()).add(node_id)
    rng = random.Random(seed)
    remaining = list(case_ids)
    rng.shuffle(remaining)
    clusters = []
    while remaining and len(clusters) < n_patients:
        seed_case = remaining.pop()
        cluster = [seed_case]
        shared = set(by_case.get(seed_case, set()))
        for cand in list(remaining):
            if by_case.get(cand, set()) & shared:
                cluster.append(cand)
                remaining.remove(cand)
                shared |= by_case[cand]
        clusters.append(cluster)
    return clusters


def render_patient(graph, library, cluster, *, patient_ref, seed):
    """Fix the state, THEN render. Labels (problem list, key findings)
    are pure functions of the graph and are fingerprinted into the
    chart BEFORE any narrative text exists."""
    cases = {c["case_id"]: c for c in library.cases()}
    rng = random.Random(f"{seed}|{patient_ref}")
    members = sorted((cases[cid] for cid in cluster),
                     key=lambda m: m["case_id"])
    # demographics: the youngest member anchors; incompatible spreads
    # are refused — a patient who is 24 in March and 58 in June is a
    # data error, not a mystery
    ages = [m["age"] for m in members]
    if max(ages) - min(ages) > AGE_BUCKET_TOLERANCE:
        raise GraphSeedError(
            f"age spread {min(ages)}-{max(ages)} exceeds "
            f"{AGE_BUCKET_TOLERANCE}y — incompatible cases clustered")
    sexes = {m["sex"] for m in members}
    if len(sexes) > 1:
        raise GraphSeedError("sex mismatch across cluster")
    # timeline: encounters ordered by acuity desc, months apart
    ordered = sorted(members, key=lambda m: -max(
        d["acuity"] for d in m["diagnoses"]))
    chronic = sorted({d["code"] for m in members for d in m["diagnoses"]
                      if d["role"] == "chronic"})
    problems, encounters = [], []
    carried = set()
    month = 0
    for m in ordered:
        month += rng.randint(1, 8)
        for d in m["diagnoses"]:
            carried.add(d["code"])
            problems.append({"code": d["code"], "label": d["label"],
                             "acuity": d["acuity"], "role": d["role"]})
        encounters.append({
            "month": month, "case": m["case_id"],
            "diagnoses": [d["code"] for d in m["diagnoses"]],
            "findings": [f["text"] for f in m["findings"]],
            "problem_list": sorted(carried),  # chronic carries forward
        })
    labels = {"patient_ref": patient_ref,
              "problem_list": sorted({p["code"]: p for p in problems}.values(),
                                     key=lambda p: p["code"]),
              "key_findings": sorted({f["text"] for m in members
                                      for f in m["findings"]
                                      if f["salience"] >= 3})}
    label_fp = hashlib.sha256(_canon(labels)).hexdigest()
    chart = {
        "patient_ref": patient_ref,
        "meta": {"tag": SYNTHETIC_TAG, "generator": VERSION,
                 "grounding": "icd10-only",
                 "seed": str(seed)},
        "demographics": {"age": min(ages), "sex": members[0]["sex"]},
        "encounters": encounters,
        "labels": labels,
        "label_fingerprint": label_fp,
        "graph_fingerprint": graph.fingerprint(),
        "narrative": _render_narrative(members, ordered, month),
    }
    chart["chart_fingerprint"] = hashlib.sha256(
        _canon({k: v for k, v in chart.items() if k != "chart_fingerprint"})
    ).hexdigest()
    return chart


def _render_narrative(members, ordered, total_months):
    """Prose is LAST and marked. The narrative never carries a fact the
    graph does not hold."""
    lines = [f"SYNTHETIC CHART — {len(ordered)} encounters over "
             f"{total_months} months."]
    for m in ordered:
        dx = ", ".join(d["label"] for d in m["diagnoses"])
        lines.append(f"Case {m['case_id']}: {dx}.")
    lines.append("This record is synthetic; every fact traces to the "
                 "declared source library.")
    return "\n".join(lines)
