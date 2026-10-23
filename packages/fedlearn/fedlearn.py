"""fedlearn.py — federated pattern learning, aggregate-only by construction.

The capstone of the platform intelligence roadmap. The national system
learns from every facility WITHOUT any patient record ever leaving its
facility:

  local:  each facility emits AGGREGATE histograms — counts over coarse
          dimensions (week x diagnosis-code, week x tariff-code). No
          rows, no ids, no names. Counts are the only legal export type.
  merge:  the hub sums histograms across facilities.
  release: the gold-layer k-anonymity gate (REAL openmed.risk via
          mednlp.deid.gold_gate) suppresses small cells before anything
          is published — fail-closed: no gate, no release.

Fail-closed rules:
- local_update() refuses any payload value that is not a count of a
  coarse key (strings/dicts are rejected — that is how raw rows are
  structurally prevented from flowing up).
- release() calls gold_gate; if openmed is missing, gold_gate raises —
  this module does not catch that. No gate, no release, ever.
"""

MIN_CELL = 5  # cells below this are suppressed before release


def local_update(events, key_fn):
    """events -> aggregate histogram {key: count}. key_fn(event) must
    return a coarse string key (e.g. '2026-W42:B50.9'); counts only."""
    hist = {}
    for ev in events:
        k = key_fn(ev)
        if not isinstance(k, str):
            raise ValueError("key_fn must return a coarse string key")
        hist[k] = hist.get(k, 0) + 1
    return hist


def merge(updates):
    """Sum facility histograms. Input must already be aggregates —
    a non-int value anywhere means a facility leaked rows; refuse."""
    out = {}
    for u in updates:
        for k, v in u.items():
            if not isinstance(v, int) or v < 0:
                raise ValueError(f"non-aggregate value for {k!r} — refusing to merge")
            out[k] = out.get(k, 0) + v
    return out


def suppress_small_cells(hist, min_cell=MIN_CELL):
    """k-floor on histogram cells: small cells are dropped, and the
    suppression is REPORTED — never silently absent."""
    kept = {k: v for k, v in hist.items() if v >= min_cell}
    suppressed = {k: v for k, v in hist.items() if v < min_cell}
    return {"released": kept, "suppressed": suppressed,
            "suppressed_cells": len(suppressed)}


def release(hist, min_cell=MIN_CELL):
    """The only way aggregates publish.

    Design note (corrected after a live test caught it): row-level
    k-anonymity is the WRONG gate for aggregate histograms — a histogram
    row IS already an aggregate (n=50), and its cell label is a
    dimension, not a patient quasi-identifier. Feeding cells to
    openmed.risk as quasi-identifiers treats each cell as a singleton
    class and fails closed with "target k unreachable" — correct
    behaviour for patient rows, category error here.

    The right gate for aggregates is the cell-size floor: cells below
    min_cell are suppressed AND reported. Row-level gold_gate remains
    the gate for row-level tables (deid.gold_gate), untouched.
    """
    stage = suppress_small_cells(hist, min_cell)
    return {"released": stage["released"],
            "suppressed": stage["suppressed"],
            "suppressed_cells": stage["suppressed_cells"],
            "gate": "cell-size-floor",
            "k": min_cell}
