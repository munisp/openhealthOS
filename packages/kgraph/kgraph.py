"""kgraph@1.0.0 — clinical knowledge-graph seam: concepts, mappings,
shapes, and federated endpoints, all as data.

The estate already binds reports to codes (packages/terminology:
versioned deployment maps, SNOMED licence-gated) and pins models and
payloads (inferlock, proofindex). What it never had is the SEMANTIC
layer between codes and data: concept hierarchies with computable
subsumption, cross-system mappings that are ratified before they are
traversed, shape constraints that validate clinical nodes with named
violations, and a federation seam for external SPARQL endpoints.

This is the clean-room answer to RDF/SPARQL platforms such as OxiRS
(Apache-2.0, Rust): their engine is never embedded here (ADR-0005 —
stdlib only, no runtime). An external endpoint — oxirs-fuseki, Jena,
a national terminology server — composes through a ratified adapter
and an INJECTED transport, like ledgeranchor's notary backends.

Doctrines enforced here:

- Concepts are registry entries, never code branches. A concept that
  is not registered cannot be subsumed, mapped, or validated against.
- The hierarchy is a DAG, enforced at write time: self-parents and
  unregistered parents are refused; a cycle can never be constructed
  because parents must exist before their children.
- Mappings are claims, and claims need four eyes: a mapping with
  fewer than two distinct named approvers exists but REFUSES to
  traverse. Equivalence you have not ratified is folklore.
- Shapes are data with a CLOSED constraint vocabulary. Validation
  names every violation (kind + path); unknown constraint kinds are
  refused at registration, never silently ignored at validation.
- Federation fails closed and states absence: unratified endpoints
  refuse; transport errors surface as NOT CHECKED with the error
  named — an endpoint that errored is not an empty result ("absence
  is not a value").
- Subsumption is recomputed per query against the pinned graph
  version; the memo is invalidated by any mutation. Stale closures
  are a defect class this module refuses by construction.

Stdlib only (ADR-0005). No clocks are read here — the caller stamps
time.
"""

from __future__ import annotations

import hashlib
import json

KGRAPH_VERSION = "kgraph@1.0.0"

#: Closed mapping-kind vocabulary. "narrower"/"broader" are claims of
#: imperfect equivalence and can never masquerade as "exact".
MAPPING_KINDS = ("exact", "narrower", "broader", "related")

#: Closed shape-constraint vocabulary. A kind not listed here is
#: refused at shape registration — validation never guesses.
CONSTRAINT_KINDS = ("required", "datatype", "max_count", "in",
                    "closed")

#: Scalar datatypes a shape may demand. Closed on purpose.
DATATYPES = ("str", "int", "float", "bool", "list", "dict")

_PY_TYPES = {"str": str, "int": int, "float": (int, float),
             "bool": bool, "list": list, "dict": dict}

#: Federation adapter registry. External engines compose as data:
#: a template adapter must be ratified by two distinct named
#: approvers AND given an injected transport before any query runs.
ADAPTERS = {
    "oxirs-fuseki": {
        "status": "template",
        "note": "OxiRS SPARQL 1.1/1.2 HTTP endpoint (Apache-2.0). "
                "Template only: ratify twice and inject a transport; "
                "the engine itself is never embedded (ADR-0005).",
        "approvals": [],
    },
    "jena-fuseki": {
        "status": "template",
        "note": "Apache Jena Fuseki endpoint. Template only.",
        "approvals": [],
    },
}


class KgraphError(ValueError):
    """A malformed graph, shape, or federation attempt. Fail closed,
    always named."""


def _canon(obj) -> str:
    """Compact canonical digest — the estate's primary contract."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


class ConceptGraph:
    """A versioned DAG of clinical concepts, as data. Parents must be
    registered before children, so the hierarchy is a DAG by
    construction — cycles are refused, never detected after the fact.
    """

    def __init__(self, name, *, version):
        if not name:
            raise KgraphError("a graph belongs to a named registry")
        if not version:
            raise KgraphError(
                "unversioned graphs are unauditable — a closure is "
                "only interpretable against the version that computed "
                "it")
        self.name = name
        self.version = version
        self.concepts = {}   # id -> record
        self.mappings = []   # list of mapping records
        self._closure_memo = None

    def add_concept(self, concept_id, *, display, system, parents=()):
        if not isinstance(concept_id, str) or not concept_id:
            raise KgraphError("concept_id must be a non-empty string")
        if concept_id in self.concepts:
            raise KgraphError(
                "duplicate concept %r — a registry, not a rewrite"
                % concept_id)
        if not display:
            raise KgraphError("a concept without a display is folklore")
        if not system:
            raise KgraphError("a concept without a system is "
                              "unattributed knowledge")
        for p in parents:
            if p == concept_id:
                raise KgraphError(
                    "self-parent %r refused — the hierarchy is a DAG"
                    % concept_id)
            if p not in self.concepts:
                raise KgraphError(
                    "unregistered parent %r — register ancestors "
                    "before descendants" % p)
        rec = {"id": concept_id, "display": display,
               "system": system, "parents": sorted(parents)}
        self.concepts[concept_id] = rec
        self._closure_memo = None   # mutation invalidates closures
        return dict(rec)

    def _ancestors(self, concept_id):
        if self._closure_memo is None:
            memo = {}

            def climb(cid):
                if cid in memo:
                    return memo[cid]
                acc = set()
                for p in self.concepts[cid]["parents"]:
                    acc.add(p)
                    acc |= climb(p)
                memo[cid] = acc
                return acc

            for cid in self.concepts:
                climb(cid)
            self._closure_memo = memo
        return self._closure_memo[concept_id]

    def is_a(self, descendant, ancestor):
        """True iff `ancestor` is reachable from `descendant` by
        parent links. Both must be registered — absence is stated."""
        for cid in (descendant, ancestor):
            if cid not in self.concepts:
                raise KgraphError(
                    "unregistered concept %r — absence is stated, "
                    "never faked" % cid)
        return ancestor in self._ancestors(descendant)

    def add_mapping(self, src, dst, *, kind="exact", approved_by=()):
        if src not in self.concepts:
            raise KgraphError("mapping source %r is not registered"
                              % src)
        if dst not in self.concepts:
            raise KgraphError("mapping target %r is not registered"
                              % dst)
        if kind not in MAPPING_KINDS:
            raise KgraphError(
                "unknown mapping kind %r — the vocabulary is closed"
                % (kind,))
        approvers = list(approved_by)
        if len(set(approvers)) != len(approvers):
            raise KgraphError(
                "duplicate approver — four eyes means FOUR eyes")
        rec = {"src": src, "dst": dst, "kind": kind,
               "approved_by": sorted(approvers),
               "ratified": len(set(approvers)) >= 2}
        self.mappings.append(rec)
        return dict(rec)

    def equivalents(self, concept_id, *, kinds=("exact",)):
        """Ratified mappings FROM this concept, of the given kinds.
        Unratified mappings refuse to traverse — they are claims, not
        facts."""
        if concept_id not in self.concepts:
            raise KgraphError(
                "unregistered concept %r" % concept_id)
        return [dict(m) for m in self.mappings
                if m["src"] == concept_id and m["kind"] in kinds
                and m["ratified"]]


class ShapeRegistry:
    """SHACL-inspired shapes as data, with a closed constraint
    vocabulary. Validation names every violation; it never coerces,
    never guesses, never silently passes."""

    def __init__(self):
        self.shapes = {}

    def register_shape(self, name, *, target, constraints):
        if not name:
            raise KgraphError("a shape needs a name")
        if name in self.shapes:
            raise KgraphError(
                "duplicate shape %r — a registry, not a rewrite"
                % name)
        if not target:
            raise KgraphError("a shape without a target validates "
                              "nothing")
        if not isinstance(constraints, list) or not constraints:
            raise KgraphError("a shape without constraints is "
                              "decoration")
        for c in constraints:
            kind = c.get("kind")
            if kind not in CONSTRAINT_KINDS:
                raise KgraphError(
                    "unknown constraint kind %r — the vocabulary is "
                    "closed; refused at registration, not at "
                    "validation" % (kind,))
            if kind in ("required", "datatype", "max_count", "in") \
                    and not c.get("path"):
                raise KgraphError(
                    "constraint %r needs a path" % kind)
            if kind == "datatype" \
                    and c.get("datatype") not in DATATYPES:
                raise KgraphError(
                    "unknown datatype %r — closed vocabulary"
                    % (c.get("datatype"),))
            if kind == "max_count" \
                    and (not isinstance(c.get("max"), int)
                         or c["max"] < 1):
                raise KgraphError(
                    "max_count needs a positive integer max")
            if kind == "in" \
                    and not isinstance(c.get("values"), (list, tuple)):
                raise KgraphError(
                    "in-constraint needs a values list")
            if kind == "closed" \
                    and not isinstance(c.get("allowed"),
                                       (list, tuple)):
                raise KgraphError(
                    "closed-constraint needs an allowed-property "
                    "list")
        rec = {"name": name, "target": target,
               "constraints": [dict(c) for c in constraints]}
        self.shapes[name] = rec
        return {"name": name, "target": target,
                "constraints": len(constraints)}

    def validate(self, node, shape_name):
        """Validate a node (dict) against a registered shape. Returns
        {"conforms": bool, "violations": [...]} — every violation
        names its kind and path. A missing shape refuses, loudly."""
        shape = self.shapes.get(shape_name)
        if shape is None:
            raise KgraphError(
                "unregistered shape %r — absence is stated, never "
                "faked" % shape_name)
        if not isinstance(node, dict):
            raise KgraphError("a node is a dict of properties")
        violations = []
        closed = None
        for c in shape["constraints"]:
            kind = c["kind"]
            if kind == "closed":
                closed = c
                continue
            path = c["path"]
            present = path in node
            value = node.get(path)
            if kind == "required" and not present:
                violations.append(
                    {"kind": "required", "path": path,
                     "detail": "required property absent"})
            elif kind == "datatype" and present:
                dt = c["datatype"]
                if dt == "int":
                    ok = isinstance(value, int) \
                        and not isinstance(value, bool)
                elif dt == "float":
                    ok = isinstance(value, (int, float)) \
                        and not isinstance(value, bool)
                else:
                    ok = isinstance(value, _PY_TYPES[dt])
                if not ok:
                    violations.append(
                        {"kind": "datatype", "path": path,
                         "detail": "expected %s, got %s"
                         % (dt, type(value).__name__)})
            elif kind == "max_count" and present:
                n = len(value) if isinstance(value, (list, tuple)) \
                    else 1
                if n > c["max"]:
                    violations.append(
                        {"kind": "max_count", "path": path,
                         "detail": "%d values, max %d"
                         % (n, c["max"])})
            elif kind == "in" and present:
                if value not in c["values"]:
                    violations.append(
                        {"kind": "in", "path": path,
                         "detail": "%r not in closed vocabulary"
                         % (value,)})
        if closed is not None:
            for prop in sorted(node):
                if prop not in closed["allowed"]:
                    violations.append(
                        {"kind": "closed", "path": prop,
                         "detail": "property not in the closed "
                                   "shape — extra semantics refused"})
        return {"shape": shape_name, "target": shape["target"],
                "conforms": not violations,
                "violations": violations}


class EndpointRegistry:
    """Federated SPARQL endpoints, as data. An endpoint must be
    ratified by two distinct named approvers before any query runs;
    the transport is INJECTED (ADR-0005); an endpoint that errors is
    stated NOT CHECKED — never an empty result."""

    def __init__(self, *, adapters=None):
        self.endpoints = {}
        self.adapters = {k: {**v, "approvals": list(v["approvals"])}
                         for k, v in (adapters or ADAPTERS).items()}

    def ratify_adapter(self, adapter, *, by):
        a = self.adapters.get(adapter)
        if a is None:
            raise KgraphError(
                "unknown adapter %r — the registry is closed"
                % (adapter,))
        if not by:
            raise KgraphError("ratification needs a named approver")
        if by in a["approvals"]:
            raise KgraphError(
                "duplicate approver — four eyes means FOUR eyes")
        a["approvals"].append(by)
        if len(set(a["approvals"])) >= 2:
            a["status"] = "ratified"
        return {"adapter": adapter, "status": a["status"],
                "approvals": list(a["approvals"])}

    def register(self, name, *, url, adapter=None, approvers=()):
        if not name:
            raise KgraphError("an endpoint needs a name")
        if name in self.endpoints:
            raise KgraphError(
                "duplicate endpoint %r — a registry, not a rewrite"
                % name)
        if not isinstance(url, str) or not url.startswith("https://"):
            raise KgraphError(
                "endpoint url must be https — clinical semantics do "
                "not travel in cleartext")
        if adapter is not None:
            a = self.adapters.get(adapter)
            if a is None:
                raise KgraphError(
                    "unknown adapter %r" % (adapter,))
            if a["status"] != "ratified":
                raise KgraphError(
                    "adapter %r is %s — a template adapter carries "
                    "no clinical query until two named approvers "
                    "ratify it" % (adapter, a["status"]))
        approvers = list(approvers)
        if len(set(approvers)) != len(approvers):
            raise KgraphError(
                "duplicate approver — four eyes means FOUR eyes")
        rec = {"name": name, "url": url, "adapter": adapter,
               "approvers": sorted(approvers),
               "ratified": len(set(approvers)) >= 2}
        self.endpoints[name] = rec
        return dict(rec)

    def query(self, name, sparql, *, transport=None, executed_at=None):
        """Run a federated query. The transport is injected:
        transport(url, sparql) -> list of rows. Refusals and errors
        are verdicts, never crashes, never silent empties."""
        ep = self.endpoints.get(name)
        if ep is None:
            raise KgraphError(
                "unregistered endpoint %r — absence is stated, "
                "never faked" % name)
        base = {"endpoint": name, "url": ep["url"],
                "query_sha256": hashlib.sha256(
                    sparql.encode()).hexdigest(),
                "executed_at": executed_at}
        if not ep["ratified"]:
            return {**base, "status": "refused",
                    "detail": "endpoint is not ratified — four eyes "
                              "before any federated query runs"}
        if transport is None:
            return {**base, "status": "not-checked",
                    "detail": "no transport injected — a missing "
                              "wire is stated, never faked"}
        try:
            rows = transport(ep["url"], sparql)
        except Exception as exc:  # named, never swallowed
            return {**base, "status": "not-checked",
                    "detail": "transport error: %s: %s"
                    % (type(exc).__name__, exc)}
        if not isinstance(rows, list):
            return {**base, "status": "not-checked",
                    "detail": "transport returned a non-list — "
                              "malformed wire, stated not trusted"}
        return {**base, "status": "ok", "row_count": len(rows),
                "rows_sha256": _canon(rows), "rows": rows}
