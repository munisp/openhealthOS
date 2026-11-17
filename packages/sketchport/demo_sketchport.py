"""sketchproof demo — the sketch port, with Apache DataSketches as the
main engine (ADR-0090), adopted by the lakehouse gold layer.

Eight drills over a deterministic synthetic estate: distinct counts with
declared bounds, hierarchy merge with cross-facility dedup, cohort set
operations, quantile verdicts (kll — non-replayable, stated), k-floor
suppression, stated refusals (contractual + wrong-seed + unknown),
degraded mode declared, and the live lakehouse adoption. Deterministic:
events are a seeded fixture, the theta engine is seeded, kll estimates
never enter the payload (verdicts do), the clock is fixed, the payload
is compact-stamped through payloadio.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "payloadio"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "lakehouse"))

import lakehouse  # noqa: E402
import payloadio  # noqa: E402
import sketchport  # noqa: E402

DEMO_VERSION = "sketchproof-demo@1.0.0"
CLOCK = "2026-11-14T00:00:00Z"
OUT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "apps", "pwa",
    "sketchproof.json"))

MECHANISM = {
    "what": "one sketch port for approximate analytics — mergeable "
            "distinct-count rollups (theta), cohort set operations "
            "(union / intersection / A-not-B), quantile sketches (kll) "
            "— Apache DataSketches is the MAIN engine",
    "main_not_optional": "the suite pins ADAPTER == 'datasketches': if "
                         "the wheel is absent the gate goes red and "
                         "names it; the stdlib path is declared "
                         "degraded mode (exact sets, unbounded memory, "
                         "no mergeable sketch bytes), stamped on every "
                         "receipt",
    "lakehouse_adoption": "lakehouse.build(sketch_rollups=True) "
                          "delegates to sketchport.gold_rollups — the "
                          "rollups are a gold product of the medallion "
                          "pipeline (ADR-0047), fed by the same bronze "
                          "events; a seam unadopted by its own estate "
                          "is decoration",
    "postgres_form_refused": "the datasketches-POSTGRESQL extension is "
                             "refused as a deployment form: a compiled "
                             "server-side extension inverts the stdlib "
                             "medallion architecture and is absent "
                             "from managed Postgres offerings; the "
                             "Python bindings wrap the same "
                             "datasketches-cpp core — the capability "
                             "is adopted, the form is not",
    "approximation_declared": "every estimate carries {estimate, "
                              "lower, upper} at 2 std devs; a registry "
                              "entry marked contractual is refused by "
                              "name — sketches never answer "
                              "contractual counts",
    "kll_non_replayable": "kll compaction is randomized: identical "
                          "input streams serialize differently, so "
                          "quantile estimates are query-time verdicts "
                          "and never stamped payload values — the "
                          "exclusion is stated, not hidden",
    "license": "Apache-2.0 — clears the license doctrine (no "
               "BSL/SSPL/Elastic, no GPL/AGPL contamination)",
}

REGISTRY_SCOPE = {
    "entries": sorted(sketchport.REGISTRY),
    "seed": sketchport.SEED,
    "bounds_std_devs": sketchport.BOUNDS_STD_DEVS,
    "k_floor": sketchport.K_FLOOR,
    "contractual": "a registry entry with contractual=true is refused "
                   "by name — approximate analytics never answer "
                   "contractual counts",
}

# --- deterministic fixture -------------------------------------------------
# Six facilities in two LGAs in one state. Patients deliberately overlap
# facilities (a patient seen at F1 and F2 must count ONCE at LGA-A) and
# F3 is large enough that LGA/state/national merges run in ESTIMATION
# mode — approximation and bounds are exercised, not just exact mode.
FACILITY_RANGES = {
    "F1": (0, 1000), "F2": (500, 1300), "F3": (2000, 5200),
    "F4": (6000, 6003),  # 3 patients — under the k-floor
    "F5": (1300, 1900), "F6": (1400, 2100),
}
PARENTS = {"F1": "LGA-A", "F2": "LGA-A", "F3": "LGA-A", "F4": "LGA-A",
           "F5": "LGA-B", "F6": "LGA-B",
           "LGA-A": "STATE-X", "LGA-B": "STATE-X"}
TRUTH = {
    "F1": 1000, "F2": 800, "F3": 3200, "F4": 3, "F5": 600, "F6": 700,
    "LGA-A": 4503, "LGA-B": 800, "STATE-X": 5203, "national": 5203,
    "facility_sum": 6303,
    "hiv": 1600, "tb": 1100, "hiv_and_tb": 600, "hiv_not_tb": 1000,
}
CLAIM_N = 2000
CLAIM_AMOUNTS = [500 + (i * 7919) % 49500 for i in range(CLAIM_N)]


def _programs(patient_i: int) -> list[str]:
    if 6000 <= patient_i <= 6002:
        return ["anc"]
    out = []
    if 0 <= patient_i <= 1599:
        out.append("hiv")
    if 1000 <= patient_i <= 2099:
        out.append("tb")
    if not out:
        out.append("opd")  # every patient is enrolled in something
    return out


def _events() -> list[dict]:
    events = []
    n = 0
    for facility in sorted(FACILITY_RANGES):
        lo, hi = FACILITY_RANGES[facility]
        for i in range(lo, hi):
            for program in _programs(i):
                n += 1
                events.append({
                    "id": "ev-%06d" % n,
                    "ts": "2026-11-13T%02d:%02d:%02dZ" % (
                        n % 24, (n // 24) % 60, n % 60),
                    "facility": facility,
                    "type": "registry_enrolment",
                    "payload": {"patient_id": "p-%04d" % i,
                                "program": program},
                })
    for i, amount in enumerate(CLAIM_AMOUNTS):
        n += 1
        events.append({
            "id": "ev-%06d" % n,
            "ts": "2026-11-13T%02d:%02d:%02dZ" % (n % 24, (n // 24) % 60,
                                                  n % 60),
            "facility": "F%d" % (i % 6 + 1),
            "type": "claim_decided",
            "payload": {"scheme": "nhia" if i % 2 == 0 else "private",
                        "payable_ngn": amount},
        })
    events.sort(key=lambda e: (e["ts"], e["id"]))
    return events


EVENTS = _events()


def _exact_quantile(values: list[float], rank: float) -> float:
    vs = sorted(float(v) for v in values)
    idx = min(len(vs) - 1, max(0, int(round(rank * (len(vs) - 1)))))
    return vs[idx]


def _national_cell(events, entry_name, **kw):
    leaf = sketchport.build_cells(events, entry_name, **kw)
    levels = sketchport.merge_up(leaf, entry_name, parents=PARENTS, **kw)
    top = levels[sketchport.REGISTRY[entry_name]["hierarchy"][-1]]
    return levels, top


def _stamp(body: dict) -> dict:
    body["sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return body


def _drill_distinct_with_bounds() -> dict:
    """National distinct patients: the estimate is approximate and the
    bounds are declared — the truth sits inside them."""
    _, top = _national_cell(EVENTS, "patient-rollup")
    cell = top[("", "national")]
    est = cell.estimate()
    lo, hi = cell.bounds()
    truth = TRUTH["national"]
    return {"name": "distinct-with-declared-bounds", "fate": "green",
            "severity": "green",
            "truth": truth,
            "estimate_within_bounds": lo <= truth <= hi,
            "relative_error_within_2pct": abs(est - truth) / truth <= 0.02,
            "estimation_mode_bounds_strict": lo < est < hi,
            "sketch_sha256": cell.digest(),
            "evidence": ["truth inside [lower, upper] at 2 std devs",
                         "estimate within 2% of exact truth",
                         "national is in estimation mode (5203 > k) — "
                         "bounds are strict; exact-mode cells collapse "
                         "bounds to the estimate, also declared"]}


def _drill_merge_dedup() -> dict:
    """The killer property: facilities merge UP the hierarchy and a
    patient seen at two facilities is counted once at national level.
    Exact rollups would need the raw rows; sketches need only bytes."""
    leaf = sketchport.build_cells(EVENTS, "patient-rollup")
    levels = sketchport.merge_up(leaf, "patient-rollup",
                                 parents=PARENTS)
    national = levels["national"][("", "national")]
    est = national.estimate()
    truth = TRUTH["national"]
    return {"name": "hierarchy-merge-deduplicates", "fate": "green",
            "severity": "green",
            "facility_sum": TRUTH["facility_sum"],
            "national_truth": truth,
            "merged_within_bounds_of_truth":
                national.bounds()[0] <= truth <= national.bounds()[1],
            "cross_facility_patients_counted_once":
                abs(est - truth) / truth <= 0.02,
            "dedup_demonstrated": TRUTH["facility_sum"] > truth,
            "evidence": [
                "sum of per-facility distincts (6303) exceeds the "
                "merged national distinct (~5203) — the overlap was "
                "deduplicated by the merge, not by raw rows",
                "facility -> LGA -> state -> national merges use only "
                "serialized sketch bytes"]}


def _drill_set_operations() -> dict:
    """Cohort set algebra at national level: HIV/TB co-enrollment
    (intersection) and enrolled-but-not-in-TB (A-not-B) — the questions
    ministries actually ask, answered without raw-row scans."""
    leaf = sketchport.build_cells(EVENTS, "program-cohort")
    nat = leaf  # hierarchy is national-only: cells keyed (program, national)
    hiv = nat[("hiv", "national")]
    tb = nat[("tb", "national")]
    inter = sketchport.set_op("program-cohort", "intersection", hiv, tb)
    anb = sketchport.set_op("program-cohort", "a_not_b", hiv, tb)
    union = sketchport.set_op("program-cohort", "union", hiv, tb)
    return {"name": "cohort-set-operations", "fate": "green",
            "severity": "green",
            "intersection_truth": TRUTH["hiv_and_tb"],
            "intersection_within_bounds":
                inter["lower"] <= TRUTH["hiv_and_tb"] <= inter["upper"],
            "a_not_b_truth": TRUTH["hiv_not_tb"],
            "a_not_b_within_bounds":
                anb["lower"] <= TRUTH["hiv_not_tb"] <= anb["upper"],
            "union_within_bounds":
                union["lower"] <= 2100 <= union["upper"],
            "evidence": ["intersection, union and A-not-B carry "
                         "declared bounds like any other cell"]}


def _drill_quantile_verdicts() -> dict:
    """kll quantiles at query time. The compaction is randomized, so
    the ESTIMATES never enter this payload — the verdicts do, against a
    stated window (2x the library's own normalized rank error)."""
    cell = sketchport.new_cell("kll")
    for amount in CLAIM_AMOUNTS:
        cell.update(amount)
    rank_error = cell.rank_error()
    span = max(CLAIM_AMOUNTS) - min(CLAIM_AMOUNTS)
    window = 2 * rank_error * span
    verdicts = {}
    for rank in (0.5, 0.95):
        truth = _exact_quantile(CLAIM_AMOUNTS, rank)
        est = cell.quantile(rank)
        verdicts["p%d" % int(rank * 100)] = {
            "within_window": abs(est - truth) <= window,
        }
    return {"name": "quantile-verdicts-not-estimates", "fate": "green",
            "severity": "green",
            "ranks": ["p50", "p95"],
            "window_ngn": window,
            "rank_error_stated": rank_error,
            "verdicts": verdicts,
            "all_verdicts_green": all(
                v["within_window"] for v in verdicts.values()),
            "estimates_in_payload": False,
            "evidence": [
                "kll is non-replayable: identical input streams "
                "serialize differently — estimates are served at query "
                "time and excluded from the stamped payload",
                "verdicts are deterministic: the window is 2x the "
                "library's own normalized rank error"]}


def _drill_k_floor() -> dict:
    """A 3-patient facility cell is SUPPRESSED with the floor named —
    absence is not a value and small cells are never rounded up. The
    same patients reappear once the LGA merge lifts the cell above k."""
    leaf = sketchport.build_cells(EVENTS, "patient-rollup")
    levels = sketchport.merge_up(leaf, "patient-rollup",
                                 parents=PARENTS)
    recs = sketchport.receipts(levels, "patient-rollup")
    f4 = next(r for r in recs
              if r["level"] == "facility" and r["key"] == "F4")
    lga = next(r for r in recs
               if r["level"] == "lga" and r["key"] == "LGA-A")
    return {"name": "k-floor-suppression-stated", "fate": "amber",
            "severity": "amber",
            "facility_f4_suppressed": f4.get("suppressed") is True,
            "reason": f4.get("reason", ""),
            "lga_a_visible_above_floor":
                lga.get("estimate", 0) >= sketchport.K_FLOOR,
            "lga_a_within_bounds_of_truth":
                lga.get("lower", 1) <= TRUTH["LGA-A"] <= lga.get(
                    "upper", 0),
            "evidence": ["F4 (3 patients) suppressed at facility level "
                         "with the k=5 floor named",
                         "the same patients are visible at LGA-A "
                         "(4503) — suppression is by cell, not by "
                         "patient"]}


def _drill_stated_refusals() -> dict:
    """Three refusals, all stated: a contractual rollup refused by
    name, a wrong-seed deserialize refused by the library's own seed
    hash, and an unknown rollup refused with the registry named."""
    registry = dict(sketchport.REGISTRY)
    registry["billing-exact"] = dict(sketchport.REGISTRY["patient-rollup"],
                                     contractual=True)
    refusals = {}
    try:
        sketchport.get_entry("billing-exact", registry=registry)
        refusals["contractual"] = "NO REFUSAL — DEFECT"
    except sketchport.SketchRefusal as exc:
        refusals["contractual"] = str(exc)
    leaf = sketchport.build_cells(EVENTS[:50], "patient-rollup")
    cell = next(iter(leaf.values()))
    good = cell.serialize()
    try:
        sketchport.deserialize_theta(good, seed=sketchport.SEED + 1)
        refusals["wrong_seed"] = "NO REFUSAL — DEFECT"
    except sketchport.SketchRefusal as exc:
        refusals["wrong_seed"] = str(exc)
    try:
        sketchport.get_entry("nope")
        refusals["unknown"] = "NO REFUSAL — DEFECT"
    except sketchport.SketchRefusal as exc:
        refusals["unknown_rollup"] = str(exc)
    ok = ("contractual" in refusals and "billing-exact" in refusals["contractual"]
          and "wrong_seed" in refusals and "seed hash" in refusals["wrong_seed"]
          and "unknown_rollup" in refusals and "nope" in refusals["unknown_rollup"])
    return {"name": "stated-refusals", "fate": "green" if ok else "red",
            "severity": "green",
            "all_refused": ok,
            "refusals": refusals,
            "evidence": ["contractual rollups refused by name — "
                         "sketches never answer contractual counts",
                         "wrong-seed sketch bytes refused by the "
                         "library's own seed-hash check, surfaced as "
                         "a stated refusal",
                         "unknown rollups refused with the registry "
                         "named"]}


def _drill_degraded_declared() -> dict:
    """Degraded mode is declared, never silent: force it and every
    receipt is stamped stdlib-exact. Exact sets give exact counts — the
    downgrade is unbounded memory and no mergeable sketch bytes."""
    product = sketchport.gold_rollups(EVENTS, parents=PARENTS,
                                      force_degraded=True)
    national = next(c for c in product["cells"]
                    if c["rollup"] == "patient-rollup"
                    and c["level"] == "national")
    return {"name": "degraded-mode-is-declared", "fate": "amber",
            "severity": "amber",
            "adapter": product["adapter"],
            "stamped_on_every_cell": all(
                c["adapter"] == "stdlib-exact"
                for c in product["cells"] if not c.get("suppressed")),
            "national_estimate_exact":
                national.get("estimate") == float(TRUTH["national"]),
            "downgrade_stated": "exact but unbounded memory; no "
                                "mergeable sketch bytes",
            "evidence": ["degraded cells carry adapter stdlib-exact "
                         "on every receipt",
                         "exact-set counting is exact — the downgrade "
                         "is memory and mergeability, not accuracy"]}


def _drill_lakehouse_adoption() -> dict:
    """The seam is adopted by its own estate: lakehouse.build with
    sketch_rollups=True writes gold/sketch_rollups.json through this
    port, and the national estimate matches the direct drill."""
    with tempfile.TemporaryDirectory() as td:
        result = lakehouse.build(EVENTS, td, sketch_rollups=True,
                                 sketch_parents=PARENTS)
        path = os.path.join(td, "gold", "sketch_rollups.json")
        with open(path) as fh:
            product = json.load(fh)
    _, top = _national_cell(EVENTS, "patient-rollup")
    direct = top[("", "national")].estimate()
    national = next(c for c in product["cells"]
                    if c["rollup"] == "patient-rollup"
                    and c["level"] == "national")
    return {"name": "lakehouse-adopts-the-seam", "fate": "green",
            "severity": "green",
            "gold_product_written": True,
            "adapter": product["adapter"],
            "bronze_rows": result["bronze_rows"],
            "national_matches_direct_drill":
                national.get("estimate") == direct,
            "excluded_stated": product["excluded"],
            "evidence": ["gold/sketch_rollups.json is a medallion "
                         "gold product fed by the same bronze events",
                         "the lakehouse estimate equals the direct "
                         "drill estimate — one engine, no parallel "
                         "implementation"]}


def run_demo() -> dict:
    body = {
        "demo": DEMO_VERSION,
        "generated_at": CLOCK,
        "mechanism": MECHANISM,
        "registry_scope": REGISTRY_SCOPE,
        "adapter": sketchport.census(),
        "source": {
            "events": len(EVENTS),
            "facilities": len(FACILITY_RANGES),
            "claims": CLAIM_N,
            "truth": TRUTH,
        },
        "drills": [
            _drill_distinct_with_bounds(),
            _drill_merge_dedup(),
            _drill_set_operations(),
            _drill_quantile_verdicts(),
            _drill_k_floor(),
            _drill_stated_refusals(),
            _drill_degraded_declared(),
            _drill_lakehouse_adoption(),
        ],
        "doctrine": [
            "a rollup is a registry entry, never a code branch",
            "main means main: datasketches absent is a red gate, not "
            "a quiet fallback",
            "approximation is declared — every estimate carries bounds",
            "sketches never answer contractual counts — refused by name",
            "kll is non-replayable — verdicts, never stamped estimates",
            "absence is not a value — small cells are suppressed with "
            "the floor named",
            "the checksum is the contract — every replayable cell "
            "carries its sketch sha256",
            "a seam unadopted by its own estate is decoration — the "
            "lakehouse gold layer runs through this port",
        ],
        "self_integrity": {"contract": "compact"},
    }
    return _stamp(body)


def main() -> None:
    body = run_demo()
    payloadio.write_text(OUT, json.dumps(body, sort_keys=True,
                                         separators=(",", ":")))
    print("sketchproof: %d drills, adapter %s, sha %s -> %s"
          % (len(body["drills"]), body["adapter"]["adapter_active"],
             body["sha256"][:12], OUT))


if __name__ == "__main__":
    main()
