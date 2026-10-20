#!/usr/bin/env python3
"""stocktake — barcode/QR cycle-count sessions vs ERP book stock (ADR-0052).

stockcheck (ADR-0018) reconciles *paper flows* (claims vs dispensing vs
procurement). stocktake reconciles *shelf vs ledger*: staff walk the
pharmacy/store/theatre with a scanner, every scan is one count event, and
the session closes into a variance report plus a ready-to-post ERPNext
Stock Reconciliation draft (composed via the REST bridge — never embedded).

Scan stream (JSONL), one event per line:
  {"session":"ST-1","item_code":"PCM-500","barcode":"8901234567890",
   "qty":48,"actor":"nurse.ada","location":"pharmacy-a",
   "at":"2026-10-20T09:14:00Z"}

  - barcode is the scanned symbology; item_code is optional when a
    barcode→item map is provided (GTIN/EAN/Code-128 all carried opaquely).
  - qty may be omitted → each scan counts as 1 unit (sweep mode).
  - duplicate scans of the same item accumulate (that's the point of a
    count), but identical scan lines (same item+barcode+actor within
    --dup-window seconds) are flagged as probable double-scans and
    excluded from the count with a warning.

Book stock (JSONL or ERPNext Bin export):
  {"item_code":"PCM-500","qty":120,"warehouse":"pharmacy-a"}

Verdicts per item: MATCH (|var|<=tolerance), SHORT, OVER, UNCOUNTED
(on the books, never scanned), UNKNOWN (scanned, not on the books).
Output: report JSON + stock-reconciliation draft dict for
erpnext_bridge (doctype "Stock Reconciliation", difference_qty signed).

CLI:
  python3 stocktake.py count --scans scans.jsonl --book book.jsonl \
      [--barcode-map map.jsonl] [--tolerance-units 0] [--tolerance-pct 0] \
      [--session ST-1] [--recon-out recon.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field

MATCH, SHORT, OVER, UNCOUNTED, UNKNOWN = "MATCH", "SHORT", "OVER", "UNCOUNTED", "UNKNOWN"


def read_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    try:
        with open(path) as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    print(f"stocktake: skipping malformed line {ln} in {path}", file=sys.stderr)
    except FileNotFoundError:
        print(f"stocktake: {path} not found — treated as empty", file=sys.stderr)
    return rows


@dataclass
class ScanEvent:
    session: str
    item_code: str
    barcode: str
    qty: float
    actor: str
    location: str
    at: str

    @staticmethod
    def parse(raw: dict) -> "ScanEvent | None":
        item = (raw.get("item_code") or "").strip()
        barcode = (raw.get("barcode") or "").strip()
        if not item and not barcode:
            return None  # a scan with no identity is noise
        try:
            qty = float(raw.get("qty", 1))
        except (TypeError, ValueError):
            return None
        if qty <= 0:
            return None
        return ScanEvent(
            session=str(raw.get("session", "")),
            item_code=item, barcode=barcode, qty=qty,
            actor=str(raw.get("actor", "")), location=str(raw.get("location", "")),
            at=str(raw.get("at", "")),
        )


def apply_barcode_map(events: list[ScanEvent], mapping: dict[str, str]) -> list[ScanEvent]:
    """Resolve barcode→item_code where the scan didn't carry one."""
    for ev in events:
        if not ev.item_code and ev.barcode in mapping:
            ev.item_code = mapping[ev.barcode]
    return events


def drop_double_scans(events: list[ScanEvent], window_sec: int) -> tuple[list[ScanEvent], list[dict]]:
    """Same item+barcode+actor within window_sec → keep first, warn rest.

    A nurse counting 48 boxes scans 48 *different* boxes; the same barcode
    twice in 3 seconds is a trigger-happy scanner, not two boxes.
    """
    from datetime import datetime

    def ts(s: str) -> float:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    kept: list[ScanEvent] = []
    warned: list[dict] = []
    last: dict[tuple[str, str, str], float] = {}
    for ev in events:
        key = (ev.item_code, ev.barcode, ev.actor)
        t = ts(ev.at)
        if key in last and t - last[key] <= window_sec and ev.qty == 1:
            warned.append({"item_code": ev.item_code, "barcode": ev.barcode,
                           "actor": ev.actor, "at": ev.at, "reason": "probable double-scan"})
            continue
        last[key] = t
        kept.append(ev)
    return kept, warned


@dataclass
class Variance:
    item_code: str
    book: float
    counted: float
    difference: float
    verdict: str


def reconcile(book: dict[str, float], counts: dict[str, float],
              tol_units: float, tol_pct: float) -> list[Variance]:
    out: list[Variance] = []
    for item in sorted(set(book) | set(counts)):
        b, c = book.get(item, 0.0), counts.get(item, 0.0)
        diff = c - b
        tol = max(tol_units, abs(b) * tol_pct / 100.0)
        if item not in book:
            verdict = UNKNOWN
        elif item not in counts:
            verdict = UNCOUNTED
        elif abs(diff) <= tol:
            verdict = MATCH
        elif diff < 0:
            verdict = SHORT
        else:
            verdict = OVER
        out.append(Variance(item, b, c, diff, verdict))
    return out


def reconciliation_draft(variances: list[Variance], warehouse: str,
                         posting_date: str) -> dict:
    """ERPNext Stock Reconciliation doc — posted via erpnext_bridge, which
    handles idempotency + offline spool. Only non-MATCH rows become items:
    the ERP should see the corrections, not the confirmations."""
    items = [
        {"item_code": v.item_code, "warehouse": warehouse, "qty": v.counted}
        for v in variances if v.verdict in (SHORT, OVER, UNKNOWN, UNCOUNTED)
    ]
    return {
        "doctype": "Stock Reconciliation",
        "purpose": "Stock Reconciliation",
        "posting_date": posting_date,
        "openhealth_id": f"stocktake:{posting_date}:{warehouse}",
        "items": items,
    }


def summarize(variances: list[Variance]) -> dict:
    by: dict[str, int] = defaultdict(int)
    for v in variances:
        by[v.verdict] += 1
    return {
        "items": len(variances),
        "verdicts": dict(sorted(by.items())),
        "short_units": round(sum(-v.difference for v in variances if v.verdict == SHORT), 3),
        "over_units": round(sum(v.difference for v in variances if v.verdict == OVER), 3),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stocktake")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count")
    c.add_argument("--scans", required=True)
    c.add_argument("--book", required=True)
    c.add_argument("--barcode-map", default="")
    c.add_argument("--tolerance-units", type=float, default=0.0)
    c.add_argument("--tolerance-pct", type=float, default=0.0)
    c.add_argument("--dup-window", type=int, default=3)
    c.add_argument("--session", default="")
    c.add_argument("--warehouse", default="")
    c.add_argument("--recon-out", default="")
    args = ap.parse_args(argv)

    events = [e for e in (ScanEvent.parse(r) for r in read_jsonl(args.scans)) if e]
    if args.session:
        events = [e for e in events if e.session == args.session]
    if args.barcode_map:
        mapping = {str(m.get("barcode", "")): str(m.get("item_code", ""))
                   for m in read_jsonl(args.barcode_map)}
        events = apply_barcode_map(events, mapping)
    events, warnings = drop_double_scans(events, args.dup_window)

    counts: dict[str, float] = defaultdict(float)
    for ev in events:
        if ev.item_code:
            counts[ev.item_code] += ev.qty
    unidentified = sum(1 for ev in events if not ev.item_code)

    book = {str(r.get("item_code", "")): float(r.get("qty", 0))
            for r in read_jsonl(args.book) if r.get("item_code")}
    variances = reconcile(book, counts, args.tolerance_units, args.tolerance_pct)

    report = {
        "summary": summarize(variances),
        "scans": len(events), "double_scan_warnings": warnings,
        "unidentified_barcodes": unidentified,
        "variances": [vars(v) for v in variances],
    }
    json.dump(report, sys.stdout, indent=2)
    print()

    if args.recon_out:
        from datetime import date
        draft = reconciliation_draft(variances, args.warehouse, str(date.today()))
        with open(args.recon_out, "w") as fh:
            json.dump(draft, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
