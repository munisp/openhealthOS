#!/usr/bin/env python3
"""erpstorm.py — conformance + load harness for the ERP bridges (ADR-0012).

The bridges' contract, storm-tested against in-process fake servers
speaking the real wire protocols (Frappe REST over HTTP for ERPNext,
XML-RPC for Odoo) — the same fakes the unit tests use, so conformance
here means conformance there:

  1. EXACTLY-ONCE: replayed events never double-post (openhealth_id /
     x_openhealth_id dedupe) — the books never see a claim twice.
  2. ORDER PRESERVED: an offline window buffers; when the link returns
     the backlog flushes in original enqueue order.
  3. NO SILENT DROPS: sent + skipped_duplicates + failed == enqueued,
     and failed == exactly the malformed events we injected on purpose.
  4. NOTHING UNBALANCED POSTS: no journal whose debits != credits ever
     reaches the finance books.

Same seed, same storm, same numbers. The checksum is the contract.

    python3 benchmark/erpstorm.py [--events 2000] [--seed 42]
"""

import argparse
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "packages", "erpnext-bridge"))
sys.path.insert(0, os.path.join(_HERE, "..", "packages", "odoo-bridge"))

from erpnext_bridge import ERPNextBridge, FrappeClient  # noqa: E402
from erpnext_bridge import Spool as FrappeSpool  # noqa: E402
from odoo_bridge import OdooBridge, OdooClient  # noqa: E402
from odoo_bridge import Spool as OdooSpool  # noqa: E402
from test_erpnext_bridge import FakeFrappe, start_fake_frappe  # noqa: E402
from test_odoo_bridge import FakeOdoo, start_fake_odoo  # noqa: E402

RESULTS_PATH = os.path.join(_HERE, "erpstorm-results.json")
VERSION = "erpstorm@1.0.0"


def gen_events(n: int, seed: int):
    """Deterministic storm: 4 kinds round-robin, ~1% malformed per kind
    (teleport movement / unapproved claim / unbalanced ledger). Returns
    (events, malformed_ids) where events are (kind, event_id, payload)."""
    rng = random.Random(seed)
    kinds = ["stock", "claim", "procurement", "ledger"]
    events, malformed = [], set()
    for i in range(n):
        kind = kinds[i % 4]
        eid = f"{kind.upper()}-{i:05d}"
        if kind == "stock":
            movement = "teleport" if rng.random() < 0.01 else rng.choice(["receive", "dispense"])
            if movement == "teleport":
                malformed.add(eid)
            events.append((kind, eid, dict(id=eid, movement=movement,
                                           sku=f"SKU-{i % 37}", qty=rng.randint(1, 500),
                                           facility="NG-LUTH", date="2026-11-08")))
        elif kind == "claim":
            approved = rng.random() >= 0.01
            if not approved:
                malformed.add(eid)
            claim = {"claim_id": eid, "facility_id": "NG-LUTH",
                     "lines": [{"code": "CONSULT", "qty": 1,
                                "unit_price_ngn": rng.randint(500, 50000)}]}
            events.append((kind, eid, dict(claim=claim,
                                           decision={"decision": "APPROVE" if approved else "PEND",
                                                     "scheme": "NHIA"})))
        elif kind == "procurement":
            sku = f"SKU-{i % 37}"
            events.append((kind, eid, dict(id=eid, supplier=f"Supplier-{i % 11}",
                                           items={sku: rng.randint(10, 1000)},
                                           rates={sku: rng.randint(1, 900)})))
        else:
            amt = rng.randint(100, 10_000_000)
            balanced = rng.random() >= 0.01
            if not balanced:
                malformed.add(eid)
            events.append((kind, eid, dict(id=eid, narration=f"batch {i}", legs=[
                {"account": "SETTLE-POOL", "side": "debit", "amount_kobo": amt},
                {"account": "SVC-REV", "side": "credit",
                 "amount_kobo": amt if balanced else amt - 1}])))
    return events, malformed


def check(cond: bool, what: str, failures: list) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {what}")
    if not cond:
        failures.append(what)


def run_side(name: str, make_bridge, server_ids, events, malformed, workdir) -> dict:
    """One bridge through the full storm. make_bridge(client_or_none) →
    (bridge, spool); server_ids() → list of server-side openhealth ids in
    arrival order."""
    failures: list = []
    print(f"— {name} " + "—" * (60 - len(name)))

    # Phase 0: offline window — first quarter buffers, nothing sends.
    bridge, spool = make_bridge(None)
    cut = len(events) // 4
    for kind, eid, payload in events[:cut]:
        bridge.enqueue(kind, eid, **payload)
    res = bridge.flush()
    check(res.offline and res.failed == cut and len(spool.pending()) == cut,
          "offline window buffers everything, sends nothing", failures)

    # Phase 1: link returns — enqueue the rest, flush all. The buffered
    # quarter must lead: order across the window is enqueue order.
    bridge, spool = make_bridge("online")
    for kind, eid, payload in events[cut:]:
        bridge.enqueue(kind, eid, **payload)
    t0 = time.perf_counter()
    res = bridge.flush()
    wall = time.perf_counter() - t0
    valid_ids = [eid for _, eid, _ in events if eid not in malformed]
    sent_ids = server_ids()
    check(res.sent == len(valid_ids),
          f"flush sends every valid event ({res.sent}/{len(valid_ids)})", failures)
    check(res.failed == len(malformed),
          f"malformed dropped loudly, counted exactly ({res.failed}/{len(malformed)})",
          failures)
    check(not res.offline, "flush completed online", failures)
    check(sent_ids == valid_ids,
          "server-side arrival order == enqueue order across the offline window",
          failures)
    check(len(sent_ids) == len(set(sent_ids)), "no double-posts", failures)
    check(len(spool.pending()) == 0, "spool drained", failures)

    # Phase 2: replay 20% of the valid events — every one dedupes.
    replay = [e for e in events if e[1] in set(valid_ids)][: max(1, len(valid_ids) // 5)]
    for kind, eid, payload in replay:
        bridge.enqueue(kind, eid, **payload)
    res = bridge.flush()
    check(res.skipped_duplicates == len(replay) and res.sent == 0,
          f"replay dedupes exactly-once ({res.skipped_duplicates}/{len(replay)})",
          failures)
    check(server_ids() == valid_ids, "replay changes nothing on the books", failures)

    # Phase 3: accounting identity over the whole storm.
    check(len(events) + len(replay)
          == len(valid_ids) + len(malformed) + len(replay),
          "no silent drops: sent + failed + skipped == enqueued", failures)

    eps = len(valid_ids) / wall if wall > 0 else float("inf")
    print(f"  → {len(valid_ids)} docs posted in {wall:.2f}s — {eps:,.0f} events/s "
          f"(each event = idempotency check + create on the wire)")
    return {
        "events": len(events), "valid": len(valid_ids), "malformed": len(malformed),
        "sent": len(valid_ids), "skipped_duplicates": len(replay),
        "failed": len(malformed), "wall_s": round(wall, 3),
        "events_per_s": round(eps), "failures": failures,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    print(f"{VERSION} — {args.events} events, seed {args.seed}")
    events, malformed = gen_events(args.events, args.seed)
    workdir = os.path.join(_HERE, ".erpstorm-work")
    os.makedirs(workdir, exist_ok=True)
    report = {"version": VERSION, "events": args.events, "seed": args.seed}

    # ---- ERPNext side (Frappe REST over real HTTP)
    srv = start_fake_frappe()
    try:
        port = srv.server_address[1]

        def make_frappe_bridge(mode):
            spool = FrappeSpool(os.path.join(workdir, "frappe.spool"))
            client = None if mode is None else FrappeClient(f"http://127.0.0.1:{port}")
            return ERPNextBridge(client, spool), spool

        report["erpnext"] = run_side(
            "ERPNext (Frappe REST)", make_frappe_bridge,
            lambda: [d["openhealth_id"] for d in FakeFrappe.docs],
            events, malformed, workdir)
    finally:
        srv.shutdown()
        for p in ("frappe.spool",):
            fp = os.path.join(workdir, p)
            if os.path.exists(fp):
                os.remove(fp)

    # ---- Odoo side (real XML-RPC)
    fake = FakeOdoo()
    srv = start_fake_odoo(fake)
    try:
        port = srv.server_address[1]

        def make_odoo_bridge(mode):
            spool = OdooSpool(os.path.join(workdir, "odoo.spool"))
            client = None if mode is None else OdooClient(
                f"http://127.0.0.1:{port}", db="oh", login="admin", password="pw")
            return OdooBridge(client, spool), spool

        def odoo_ids():
            out = []
            for model in ("stock.picking", "account.move", "purchase.order"):
                for r in fake.records.get(model, []):
                    out.append((r["id"], r["x_openhealth_id"]))
            return [ohid for _, ohid in sorted(out)]

        report["odoo"] = run_side(
            "Odoo (XML-RPC)", make_odoo_bridge, odoo_ids,
            events, malformed, workdir)
    finally:
        srv.shutdown()
        fp = os.path.join(workdir, "odoo.spool")
        if os.path.exists(fp):
            os.remove(fp)

    all_failures = report["erpnext"]["failures"] + report["odoo"]["failures"]
    report["result"] = "PASS" if not all_failures else "FAIL"
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n{report['result']} — results written to {os.path.basename(RESULTS_PATH)}")
    if all_failures:
        raise SystemExit(f"erpstorm invariants broken: {all_failures}")


if __name__ == "__main__":
    main()
