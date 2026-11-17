"""sketchport — the platform sketch/analytics port (ADR-0090).

Apache DataSketches is the MAIN sketch engine for approximate analytics
on this platform — mergeable distinct-count rollups (theta), cohort set
operations (intersection / union / A-not-B), and quantile sketches (kll).
The Python bindings wrap the same datasketches-cpp core that the
datasketches-postgresql extension wraps; the Postgres extension FORM is
refused (ADR-0090: a compiled server-side extension inverts the stdlib
medallion architecture of packages/lakehouse (ADR-0047) and is absent
from managed Postgres offerings — the capability is adopted, the
deployment form is not).

Main means main: when the datasketches wheel is importable every sketch
cell runs through it. The stdlib fallback is DECLARED degraded mode —
exact set-based counting (unbounded memory, no mergeable sketch bytes)
with the adapter stamped on every receipt. The suite pins
ADAPTER == "datasketches": wheel absent = red gate.

Doctrines carried:
  - the checksum is the contract   — every theta cell receipt carries
    sha256 of its serialized sketch bytes (deterministic given SEED)
  - approximation is declared      — every estimate carries
    {estimate, lower, upper} at BOUNDS_STD_DEVS; sketches NEVER answer
    contractual counts (a registry entry with contractual=true is
    refused by name)
  - kll is non-replayable          — randomized compaction means two
    identical input streams serialize differently; quantile estimates
    are served at query time and EXCLUDED from stamped payloads
    (stated exclusion, not hidden)
  - absence is not a value         — cells under the k-anonymity floor
    are SUPPRESSED with the floor named (ADR-0083 doctrine), never
    rounded up and never silently dropped
  - fail-closed                    — wrong-seed deserialize is refused
    by the library's own seed-hash check and surfaced as SketchRefusal
  - the lakehouse adopts the seam  — lakehouse.build(sketch_rollups=
    True) delegates here; a seam unadopted by its own estate is
    decoration
"""

from __future__ import annotations

import hashlib

try:
    import datasketches as _ds
    _IMPORT_ERROR = None
except Exception as exc:  # degraded mode — declared, never silent
    _ds = None
    _IMPORT_ERROR = "%s: %s" % (type(exc).__name__, exc)

__version__ = "1.0.0"

ADAPTER_MAIN = "datasketches"
ADAPTER_DEGRADED = "stdlib-exact"

#: Which engine the port is running on RIGHT NOW. Main means main —
#: the suite pins this to ADAPTER_MAIN.
ADAPTER = ADAPTER_MAIN if _ds is not None else ADAPTER_DEGRADED
DEGRADED = ADAPTER != ADAPTER_MAIN
DEGRADED_REASON = _IMPORT_ERROR

DATASKETCHES_VERSION = None
if _ds is not None:
    try:
        from importlib.metadata import version as _pkg_version
        DATASKETCHES_VERSION = _pkg_version("datasketches")
    except Exception:
        DATASKETCHES_VERSION = None

#: Fixed registry seed — data, never a hidden constant. Theta hashing is
#: deterministic given the seed; deserialize verifies the seed hash.
SEED = 20261114

#: Bounds width for declared approximation (2 std devs ~= 95%).
BOUNDS_STD_DEVS = 2

#: Default k-anonymity floor (ADR-0083): small cells are suppressed with
#: the floor named, never rounded up.
K_FLOOR = 5

SKETCH_KINDS = ("theta", "kll")


class SketchError(Exception):
    """Base class for sketchport failures."""


class SketchRefusal(SketchError):
    """A stated refusal — the reason is the message, never silent."""


#: Rollup contracts as DATA (country/company-agnostic doctrine: a rollup
#: is a registry entry, never a code branch). id_field / measure_field
#: are dot-free keys of the event payload; event_types selects the bronze
#: event types that feed the rollup; hierarchy names the merge levels
#: from leaf to root (the caller injects the facility tree); k_floor is
#: the suppression floor; replayable=False kinds never enter stamped
#: payloads; contractual=True is refused by name.
REGISTRY = {
    "patient-rollup": {
        "kind": "theta",
        "event_types": ["registry_enrolment"],
        "id_field": "patient_id",
        "hierarchy": ["facility", "lga", "state", "national"],
        "k_floor": K_FLOOR,
        "replayable": True,
        "contractual": False,
    },
    "program-cohort": {
        "kind": "theta",
        "event_types": ["registry_enrolment"],
        "id_field": "patient_id",
        "partition_field": "program",
        "hierarchy": ["national"],
        "k_floor": K_FLOOR,
        "replayable": True,
        "contractual": False,
    },
    "claim-amounts": {
        "kind": "kll",
        "event_types": ["claim_decided"],
        "measure_field": "payable_ngn",
        "hierarchy": ["national"],
        "k_floor": K_FLOOR,
        "replayable": False,
        "contractual": False,
    },
}


# ---------------------------------------------------------------------------
# cells — one sketch per (rollup, level, key[, partition])
# ---------------------------------------------------------------------------

class _ThetaCell:
    """Datasketches-backed theta cell: mergeable, seeded, bounds-awake."""

    engine = ADAPTER_MAIN

    def __init__(self):
        self._s = _ds.update_theta_sketch(seed=SEED)
        self.n = 0

    def update(self, value) -> None:
        self._s.update(str(value))
        self.n += 1

    def merge(self, other) -> None:
        """Merge another cell OR raw sketch bytes/deserialized sketch —
        the merge needs only the bytes, never the raw rows."""
        other_s = getattr(other, "_s", other)
        u = _ds.theta_union(seed=SEED)
        u.update(self._s)
        u.update(other_s)
        self._s = u.get_result()

    def estimate(self) -> float:
        return self._s.get_estimate()

    def bounds(self) -> tuple[float, float]:
        return (self._s.get_lower_bound(BOUNDS_STD_DEVS),
                self._s.get_upper_bound(BOUNDS_STD_DEVS))

    def digest(self) -> str:
        return hashlib.sha256(self.serialize()).hexdigest()

    def serialize(self) -> bytes:
        s = self._s
        comp = s if isinstance(s, _ds.compact_theta_sketch) else s.compact()
        return comp.serialize()


class _KllCell:
    """Datasketches-backed kll cell. NON-REPLAYABLE: compaction is
    randomized, so serialized bytes and quantile estimates differ across
    identical runs. Verdicts and query-time answers only — never stamped
    payloads (stated exclusion, ADR-0090)."""

    engine = ADAPTER_MAIN
    replayable = False

    def __init__(self):
        self._s = _ds.kll_floats_sketch()
        self.n = 0

    def update(self, value) -> None:
        self._s.update(float(value))
        self.n += 1

    def merge(self, other: "_KllCell") -> None:
        self._s.merge(other._s)

    def quantile(self, rank: float) -> float:
        return self._s.get_quantile(rank)

    @staticmethod
    def rank_error() -> float:
        return _ds.kll_floats_sketch.get_normalized_rank_error(200, True)


class _ExactSetCell:
    """Degraded theta: exact set membership. Exact but unbounded in
    memory and WITHOUT mergeable sketch bytes — a declared downgrade."""

    engine = ADAPTER_DEGRADED

    def __init__(self):
        self._s: set[str] = set()
        self.n = 0

    def update(self, value) -> None:
        self._s.add(str(value))
        self.n += 1

    def merge(self, other: "_ExactSetCell") -> None:
        self._s |= other._s

    def estimate(self) -> float:
        return float(len(self._s))

    def bounds(self) -> tuple[float, float]:
        exact = float(len(self._s))
        return (exact, exact)

    def digest(self) -> str:
        joined = "\x00".join(sorted(self._s)).encode("utf-8")
        return hashlib.sha256(b"stdlib-exact:" + joined).hexdigest()


class _ExactListCell:
    """Degraded kll: sorted-list quantiles. Exact and deterministic —
    degraded only in memory footprint, not in replayability."""

    engine = ADAPTER_DEGRADED
    replayable = True  # exact values ARE replayable; stated as such

    def __init__(self):
        self._v: list[float] = []
        self.n = 0

    def update(self, value) -> None:
        self._v.append(float(value))
        self.n += 1

    def merge(self, other: "_ExactListCell") -> None:
        self._v.extend(other._v)

    def quantile(self, rank: float) -> float:
        if not self._v:
            raise SketchRefusal("quantile of an empty cell — absence is not a value")
        vs = sorted(self._v)
        idx = min(len(vs) - 1, max(0, int(round(rank * (len(vs) - 1)))))
        return vs[idx]

    @staticmethod
    def rank_error() -> float:
        return 0.0  # exact


def _new_cell(kind: str, force_degraded: bool):
    if kind == "theta":
        return _ExactSetCell() if (force_degraded or _ds is None) else _ThetaCell()
    if kind == "kll":
        return _ExactListCell() if (force_degraded or _ds is None) else _KllCell()
    raise SketchRefusal("unknown sketch kind %r — registry kinds are %s"
                        % (kind, list(SKETCH_KINDS)))


def new_cell(kind: str, force_degraded: bool = False):
    """Public cell constructor — kind is one of SKETCH_KINDS."""
    return _new_cell(kind, force_degraded)


# ---------------------------------------------------------------------------
# rollups
# ---------------------------------------------------------------------------

def get_entry(name: str, registry=None) -> dict:
    registry = REGISTRY if registry is None else registry
    entry = registry.get(name)
    if entry is None:
        raise SketchRefusal("unknown rollup %r — registry entries are %s"
                            % (name, sorted(registry)))
    if entry.get("contractual"):
        raise SketchRefusal(
            "rollup %r is contractual — sketches are approximate and never "
            "answer contractual counts; use an exact path" % name)
    return entry


def _payload_value(ev: dict, field: str):
    payload = ev.get("payload") or {}
    return payload.get(field)


def build_cells(events: list[dict], entry_name: str, registry=None,
                level: str | None = None,
                force_degraded: bool = False) -> dict:
    """Build leaf cells for a rollup entry from bronze-shaped events.

    Leaf keys come from the entry's FIRST hierarchy level (injected
    `level` overrides). Returns {(partition, leaf_key): cell}."""
    entry = get_entry(entry_name, registry)
    if level is None:
        level = entry["hierarchy"][0]
    cells: dict = {}
    wanted = set(entry["event_types"])
    for ev in events:
        if ev.get("type") not in wanted:
            continue
        leaf = "national" if level == "national" else ev.get(level, "?")
        partition = ""
        if entry.get("partition_field"):
            partition = str(_payload_value(ev, entry["partition_field"]) or "?")
        if entry["kind"] == "theta":
            value = _payload_value(ev, entry["id_field"])
        else:
            value = _payload_value(ev, entry["measure_field"])
            if not isinstance(value, (int, float)):
                continue  # non-numeric measure: skipped, never coerced
        if value is None or value == "":
            continue  # absence is not a value — never sketched
        key = (partition, leaf)
        if key not in cells:
            cells[key] = _new_cell(entry["kind"], force_degraded)
        cells[key].update(value)
    return cells


def merge_up(cells: dict, entry_name: str, registry=None,
             parents: dict | None = None,
             force_degraded: bool = False) -> dict:
    """Merge leaf cells up the entry's hierarchy. The killer property:
    a patient seen at two facilities is counted ONCE at national level
    — exact rollups need the raw rows; sketches need only the bytes."""
    entry = get_entry(entry_name, registry)
    hierarchy = entry["hierarchy"]
    levels: dict = {hierarchy[0]: dict(cells)}
    for i in range(1, len(hierarchy)):
        child_level, parent_level = hierarchy[i - 1], hierarchy[i]
        merged: dict = {}
        for (partition, key), cell in levels[child_level].items():
            if parent_level == "national":
                parent_key = "national"
            elif parents and key in parents:
                parent_key = parents[key]
            else:
                parent_key = key  # no tree injected: stated passthrough
            mkey = (partition, parent_key)
            if mkey not in merged:
                merged[mkey] = _new_cell(entry["kind"], force_degraded)
            merged[mkey].merge(cell)
        levels[parent_level] = merged
    return levels


def receipts(levels: dict, entry_name: str, registry=None,
             force_degraded: bool = False) -> list[dict]:
    """Cells -> receipts. k-floor suppression is STATED; every estimate
    carries bounds; every replayable cell carries its sketch checksum."""
    entry = get_entry(entry_name, registry)
    adapter = ADAPTER_DEGRADED if (force_degraded or _ds is None) else ADAPTER_MAIN
    out: list[dict] = []
    for level, cells in levels.items():
        for (partition, key), cell in sorted(cells.items()):
            est = cell.estimate()
            base = {
                "rollup": entry_name,
                "level": level,
                "key": key,
                "adapter": adapter,
                "seed": SEED,
            }
            if partition:
                base["partition"] = partition
            if entry["kind"] == "theta" and est < entry["k_floor"]:
                base["suppressed"] = True
                base["reason"] = ("k-floor k=%d — small cells are stated, "
                                  "never rounded up" % entry["k_floor"])
            elif entry["kind"] == "kll":
                base["n"] = cell.n
                base["replayable"] = False
                base["reason"] = ("kll compaction is randomized — estimates "
                                  "are query-time verdicts, never stamped "
                                  "payload values")
            else:
                lo, hi = cell.bounds()
                base["estimate"] = est
                base["lower"] = lo
                base["upper"] = hi
                base["n"] = cell.n
                base["sketch_sha256"] = cell.digest()
            out.append(base)
    return out


def set_op(entry_name: str, op: str, left, right, force_degraded: bool = False) -> dict:
    """Cohort set algebra between two theta cells (partition sketches).

    op is one of union / intersect / a_not_b. Result carries declared
    bounds and a checksum like any other cell."""
    get_entry(entry_name)  # refusal discipline applies to ops too
    degraded = force_degraded or _ds is None
    adapter = ADAPTER_DEGRADED if degraded else ADAPTER_MAIN
    if degraded:
        lset, rset = left._s, right._s
        if op == "union":
            rs = lset | rset
        elif op == "intersection":
            rs = lset & rset
        elif op == "a_not_b":
            rs = lset - rset
        else:
            raise SketchRefusal("unknown set op %r — ops are union, "
                                "intersection, a_not_b" % op)
        est = float(len(rs))
        return {"op": op, "adapter": adapter, "estimate": est,
                "lower": est, "upper": est,
                "sketch_sha256": hashlib.sha256(
                    b"stdlib-exact:" + "\x00".join(sorted(rs)).encode()).hexdigest()}
    if _ds is not None and not isinstance(left, _ThetaCell):
        raise SketchRefusal("set ops require theta cells, got %s"
                            % type(left).__name__)
    if op == "union":
        u = _ds.theta_union(seed=SEED)
        u.update(left._s)
        u.update(right._s)
        res = u.get_result()
    elif op == "intersection":
        inter = _ds.theta_intersection(seed=SEED)
        inter.update(left._s)
        inter.update(right._s)
        res = inter.get_result()
    elif op == "a_not_b":
        res = _ds.theta_a_not_b(seed=SEED).compute(left._s, right._s)
    else:
        raise SketchRefusal("unknown set op %r — ops are union, "
                            "intersection, a_not_b" % op)
    return {"op": op, "adapter": adapter, "estimate": res.get_estimate(),
            "lower": res.get_lower_bound(BOUNDS_STD_DEVS),
            "upper": res.get_upper_bound(BOUNDS_STD_DEVS),
            "sketch_sha256": hashlib.sha256(res.serialize()).hexdigest()}


def deserialize_theta(data: bytes, seed: int = SEED):
    """Fail-closed read: the library verifies the seed hash on
    deserialize; a mismatch is surfaced as a stated refusal."""
    if _ds is None:
        raise SketchRefusal("datasketches engine absent — degraded mode "
                            "has no sketch bytes to deserialize")
    try:
        return _ds.compact_theta_sketch.deserialize(data, seed=seed)
    except Exception as exc:
        raise SketchRefusal("sketch refused on deserialize: %s" % exc)


def gold_rollups(events: list[dict], registry=None, parents: dict | None = None,
                 force_degraded: bool = False) -> dict:
    """The lakehouse gold product: replayable theta rollups for every
    registry entry, with non-replayable kinds named as excluded —
    stated, never silent."""
    registry = REGISTRY if registry is None else registry
    adapter = ADAPTER_DEGRADED if (force_degraded or _ds is None) else ADAPTER_MAIN
    cells_out: list[dict] = []
    excluded: list[dict] = []
    for name in sorted(registry):
        entry = registry[name]
        if not entry.get("replayable", True):
            excluded.append({
                "rollup": name,
                "reason": "kind %s is non-replayable — verdicts at query "
                          "time, never stamped payload values" % entry["kind"],
            })
            continue
        leaf = build_cells(events, name, registry=registry,
                           force_degraded=force_degraded)
        levels = merge_up(leaf, name, registry=registry, parents=parents,
                          force_degraded=force_degraded)
        cells_out.extend(receipts(levels, name, registry=registry,
                                  force_degraded=force_degraded))
    return {
        "adapter": adapter,
        "seed": SEED,
        "bounds_std_devs": BOUNDS_STD_DEVS,
        "k_floor": K_FLOOR,
        "cells": cells_out,
        "excluded": excluded,
    }


def census() -> dict:
    """The live state of the port — read, never assumed."""
    return {
        "adapter_main": ADAPTER_MAIN,
        "adapter_active": ADAPTER,
        "degraded": DEGRADED,
        "degraded_reason": DEGRADED_REASON,
        "datasketches_version": DATASKETCHES_VERSION,
        "seed": SEED,
        "bounds_std_devs": BOUNDS_STD_DEVS,
        "k_floor": K_FLOOR,
        "registry_entries": len(REGISTRY),
        "kinds": list(SKETCH_KINDS),
        "replayable_entries": sorted(
            n for n, e in REGISTRY.items() if e.get("replayable", True)),
        "non_replayable_entries": sorted(
            n for n, e in REGISTRY.items() if not e.get("replayable", True)),
    }
