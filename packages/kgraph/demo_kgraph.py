"""demo_kgraph.py — kgproof-demo@1.0.0: deterministic export of the
knowledge-graph seam's proof surface.

Same doctrine as every proof exporter in the estate: synthetic DATA
only, real machinery; byte-for-byte reproducible; sha256-stamped under
the compact contract; registered in proofindex; gated by the meta
exporter-determinism suite.

Demo story: a small clinical concept graph (LOINC vitals hierarchy +
ICD-10 chapter), a ratified LOINC↔ICD mapping and an unratified one,
a closed vitals-panel shape validated against a conforming node and a
violating node, and the federation seam: unratified endpoint refused,
missing transport stated, transport error stated NOT CHECKED, a
ratified endpoint queried through an injected deterministic transport.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import kgraph  # noqa: E402

DEMO_VERSION = "kgproof-demo@1.0.0"
CLOCK = "2026-11-11T12:00:00Z"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "..", "apps", "pwa", "kgproof.json")


def _stamp(body):
    """Compact stamp contract — sha256 of the canonical string."""
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _refusal(fn):
    try:
        fn()
    except kgraph.KgraphError as exc:
        return {"outcome": "refused", "reason": str(exc)}
    return {"outcome": "NOT REFUSED — DEFECT"}


def _seed_graph():
    g = kgraph.ConceptGraph("demo-clinical", version="2026.11.11")
    g.add_concept("loinc:LP31755-9", display="Vital signs panel",
                  system="LOINC")
    g.add_concept("loinc:8480-6", display="Systolic blood pressure",
                  system="LOINC", parents=("loinc:LP31755-9",))
    g.add_concept("loinc:8462-4", display="Diastolic blood pressure",
                  system="LOINC", parents=("loinc:LP31755-9",))
    g.add_concept("loinc:9279-1", display="Respiratory rate",
                  system="LOINC", parents=("loinc:LP31755-9",))
    g.add_concept("icd10:E10-E14", display="Diabetes mellitus",
                  system="ICD-10")
    g.add_concept("icd10:E11.9",
                  display="Type 2 diabetes mellitus without "
                          "complications",
                  system="ICD-10", parents=("icd10:E10-E14",))
    g.add_concept("icd10:I10",
                  display="Essential (primary) hypertension",
                  system="ICD-10")
    return g


def _seed_shapes():
    shapes = kgraph.ShapeRegistry()
    shapes.register_shape(
        "vitals-panel@1.0.0", target="loinc:LP31755-9",
        constraints=[
            {"kind": "required", "path": "systolic"},
            {"kind": "datatype", "path": "systolic",
             "datatype": "float"},
            {"kind": "required", "path": "diastolic"},
            {"kind": "max_count", "path": "tags", "max": 4},
            {"kind": "in", "path": "position",
             "values": ["sitting", "standing", "supine"]},
            {"kind": "closed",
             "allowed": ["systolic", "diastolic", "tags",
                         "position"]},
        ])
    return shapes


def _seed_endpoints():
    eps = kgraph.EndpointRegistry()
    # An unratified endpoint: registered, but never queried.
    eps.register("national-terminology",
                 url="https://terminology.example.gov/sparql")
    # A ratified endpoint on the template OxiRS adapter — the adapter
    # itself must be ratified first (four eyes, twice).
    eps.ratify_adapter("oxirs-fuseki", by="ada.lovelace")
    eps.ratify_adapter("oxirs-fuseki", by="bola.tinubu")
    eps.register("oxirs-knowledge-base",
                 url="https://sparql.cooljapan.tech/sparql",
                 adapter="oxirs-fuseki",
                 approvers=("ada.lovelace", "bola.tinubu"))
    return eps


def _transport(url, sparql):
    """Injected deterministic transport: the wire is a pure function
    of (url, sparql) — no network in a proof surface."""
    return [{"endpoint": url,
             "query_sha256": hashlib.sha256(
                 sparql.encode()).hexdigest(),
             "answer": "synthetic-row"}]


def _broken_transport(url, sparql):
    raise ConnectionError("simulated wire failure — stated, never "
                          "swallowed")


def run_demo():
    g = _seed_graph()
    # Mappings: one ratified, one pending (one approver).
    g.add_mapping("loinc:8480-6", "icd10:I10", kind="related",
                  approved_by=("ada.lovelace", "bola.tinubu"))
    g.add_mapping("loinc:8480-6", "icd10:E11.9", kind="related",
                  approved_by=("ada.lovelace",))
    shapes = _seed_shapes()
    eps = _seed_endpoints()

    conforming = {"systolic": 128.0, "diastolic": 82.0,
                  "tags": ["resting"], "position": "sitting"}
    violating = {"diastolic": 82.0, "position": "prone",
                 "extra_semantics": "smuggled",
                 "tags": ["a", "b", "c", "d", "e"]}

    control = {
        "subsumption": {
            "sbp-is-vital": g.is_a("loinc:8480-6",
                                   "loinc:LP31755-9"),
            "e119-is-diabetes": g.is_a("icd10:E11.9",
                                       "icd10:E10-E14"),
            "sbp-is-not-diabetes": g.is_a("loinc:8480-6",
                                          "icd10:E10-E14"),
        },
        "ratified_mapping_traverses": g.equivalents(
            "loinc:8480-6", kinds=("related",)),
        "conforming_node": shapes.validate(conforming,
                                           "vitals-panel@1.0.0"),
        "ratified_query": eps.query(
            "oxirs-knowledge-base",
            "SELECT * WHERE { ?s ?p ?o } LIMIT 10",
            transport=_transport, executed_at=CLOCK),
    }

    drills = [
        {"name": "a cycle attempts to construct itself",
         "attack": "self-parent concept",
         "result": _refusal(
             lambda: g.add_concept("loinc:X", display="x",
                                   system="LOINC",
                                   parents=("loinc:X",)))},
        {"name": "a descendant appears before its ancestor",
         "attack": "unregistered parent",
         "result": _refusal(
             lambda: g.add_concept("loinc:Y", display="y",
                                   system="LOINC",
                                   parents=("loinc:GHOST",)))},
        {"name": "an unratified mapping is asked to carry meaning",
         "attack": "single-approver traversal",
         "result": {"outcome": "not-traversed",
                    "traversed": g.equivalents(
                        "icd10:E11.9", kinds=("related",)),
                    "detail": "the pending E11.9 mapping is absent "
                              "from traversal — claims need four "
                              "eyes"}},
        {"name": "a node smuggles extra semantics past a closed "
                 "shape",
         "attack": "four violations in one node",
         "result": {"outcome": "named",
                    "validation": shapes.validate(
                        violating, "vitals-panel@1.0.0")}},
        {"name": "an unratified endpoint is queried",
         "attack": "federation without four eyes",
         "result": eps.query(
             "national-terminology",
             "SELECT * WHERE { ?s ?p ?o }",
             transport=_transport, executed_at=CLOCK)},
        {"name": "a ratified endpoint has no wire",
         "attack": "missing transport",
         "result": eps.query(
             "oxirs-knowledge-base",
             "SELECT * WHERE { ?s ?p ?o } LIMIT 10",
             executed_at=CLOCK)},
        {"name": "the wire fails mid-federation",
         "attack": "transport error",
         "result": eps.query(
             "oxirs-knowledge-base",
             "SELECT * WHERE { ?s ?p ?o } LIMIT 10",
             transport=_broken_transport, executed_at=CLOCK)},
        {"name": "a template adapter is used before ratification",
         "attack": "jena-fuseki, zero approvers",
         "result": _refusal(
             lambda: eps.register(
                 "rogue-jena",
                 url="https://jena.example.org/sparql",
                 adapter="jena-fuseki",
                 approvers=("ada.lovelace", "bola.tinubu")))},
    ]

    body = {
        "surface": "kgproof",
        "demo": True,
        "banner": "Synthetic concepts only — every engine below is "
                  "production code exercising production refusals.",
        "versions": {"kgraph": kgraph.KGRAPH_VERSION,
                     "exporter": DEMO_VERSION},
        "generated_at": CLOCK,
        "registry": {
            "graph": g.name, "version": g.version,
            "concepts": len(g.concepts),
            "mappings": len(g.mappings),
            "ratified_mappings": sum(1 for m in g.mappings
                                     if m["ratified"]),
            "shapes": len(shapes.shapes),
            "endpoints": len(eps.endpoints),
        },
        "control": control,
        "drills": drills,
        "honesty": {
            "concepts_as_data": "every concept, mapping, shape and "
                                "endpoint is a registry entry — no "
                                "code branches",
            "four_eyes": "mappings and endpoints traverse only after "
                         "two distinct named approvers",
            "absence_is_not_a_value": "a transport error is stated "
                                      "NOT CHECKED — never an empty "
                                      "result",
            "engine_never_embedded": "external engines (OxiRS, Jena) "
                                     "compose via ratified adapters "
                                     "+ injected transport; nothing "
                                     "is embedded (ADR-0005)",
            "closed_vocabularies": "unknown mapping kinds and "
                                   "constraint kinds are refused at "
                                   "registration, never guessed at "
                                   "validation",
        },
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    payload = json.dumps(body, indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    payloadio.write_text(OUT, payload)
    print("kgproof: %d concepts, %d drills, sha %s -> %s"
          % (body["registry"]["concepts"], len(body["drills"]),
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
