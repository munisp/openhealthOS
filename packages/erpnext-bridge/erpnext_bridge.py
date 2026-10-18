#!/usr/bin/env python3
"""ERPNext bridge — accounting, inventory and procurement sync (ADR-0012).

ERPNext (GPL-3.0, Frappe framework) is composed over its REST API, never
embedded (ADR-0005): the platform's ledger, pharmacy stock and procurement
stays the system of record; ERPNext is the finance office's book — general
ledger, warehouse stock, supplier orders. The network boundary keeps the
licences independent, and the platform keeps working when ERPNext is down:
everything buffers in a JSONL spool and flushes when the link returns.

What flows where:
  * pharmacy stock receive/dispense  → Stock Entry (Material Receipt/Issue)
  * APPROVEd claim                   → Sales Invoice (the payer is the customer)
  * pooled procurement order         → Purchase Order
  * ledger double-entry batch        → Journal Entry

Idempotency: every document carries `openhealth_id` (a custom field on the
ERPNext side); the bridge checks-before-creating, so a crash mid-flush and
a replayed event can never double-post to the books.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


# ------------------------------------------------------------- mappers
# Pure functions: platform event → ERPNext document. Deterministic and
# unit-tested without any server.

def stock_entry(ev: dict) -> dict:
    """Pharmacy stock movement → Stock Entry."""
    movement = ev.get("movement", "")   # receive | dispense
    entry_type = {"receive": "Material Receipt", "dispense": "Material Issue"}.get(movement)
    if entry_type is None:
        raise ValueError(f"unknown stock movement: {movement!r}")
    item = {"item_code": ev["sku"], "qty": ev["qty"]}
    if movement == "receive":
        item["t_warehouse"] = ev.get("warehouse", f"Stores - {ev.get('facility', 'OH')}")
    else:
        item["s_warehouse"] = ev.get("warehouse", f"Stores - {ev.get('facility', 'OH')}")
    return {
        "doctype": "Stock Entry",
        "stock_entry_type": entry_type,
        "openhealth_id": ev["id"],
        "posting_date": ev.get("date", ""),
        "items": [item],
    }


def sales_invoice(ev: dict) -> dict:
    """APPROVEd claim → Sales Invoice; the payer is the customer.
    ev = {"claim": ..., "decision": ..., "customer": optional override}."""
    claim, decision = ev["claim"], ev["decision"]
    if decision.get("decision") != "APPROVE":
        raise ValueError("only APPROVEd claims are invoiced")
    lines = [
        {"item_code": ln["code"], "qty": ln.get("qty", 1), "rate": ln.get("unit_price_ngn", 0)}
        for ln in claim.get("lines", [])
    ]
    return {
        "doctype": "Sales Invoice",
        "customer": ev.get("customer") or decision.get("scheme", "NHIA"),
        "openhealth_id": claim["claim_id"],
        "items": lines,
        "remarks": f"NHIA/HMO claim {claim['claim_id']} — facility {claim.get('facility_id', '')}",
    }


def purchase_order(order: dict) -> dict:
    """Pooled procurement order → Purchase Order."""
    return {
        "doctype": "Purchase Order",
        "supplier": order["supplier"],
        "openhealth_id": order["id"],
        "items": [
            {"item_code": sku, "qty": qty, "rate": order.get("rates", {}).get(sku, 0)}
            for sku, qty in order.get("items", {}).items()
        ],
    }


def journal_entry(batch: dict) -> dict:
    """Ledger double-entry batch → Journal Entry. Debits must equal credits —
    the Rust ledger already enforces this; the mapper asserts it again."""
    accounts = []
    debit = credit = 0
    for leg in batch.get("legs", []):
        amt = int(leg["amount_kobo"])
        if leg["side"] == "debit":
            accounts.append({"account": leg["account"], "debit_in_account_currency": amt / 100})
            debit += amt
        else:
            accounts.append({"account": leg["account"], "credit_in_account_currency": amt / 100})
            credit += amt
    if debit != credit:
        raise ValueError(f"unbalanced batch: debits {debit} != credits {credit}")
    return {
        "doctype": "Journal Entry",
        "voucher_type": "Journal Entry",
        "openhealth_id": batch["id"],
        "accounts": accounts,
        "user_remark": batch.get("narration", "OpenHealth ledger batch"),
    }


MAPPERS = {
    "stock": stock_entry,
    "claim": sales_invoice,
    "procurement": purchase_order,
    "ledger": journal_entry,
}


# ------------------------------------------------------------- client

class FrappeOffline(IOError):
    """ERPNext unreachable — the caller buffers and retries."""


class FrappeClient:
    """Minimal Frappe REST client (stdlib only). Auth: token key:secret."""

    def __init__(self, base_url: str, key: str = "", secret: str = "", timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.token = f"token {key}:{secret}" if key else ""
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", self.token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FrappeOffline(str(e)) from e

    def exists(self, doctype: str, openhealth_id: str) -> bool:
        """Idempotency anchor: has this platform event been posted already?"""
        filters = json.dumps([["openhealth_id", "=", openhealth_id]])
        path = ("/api/resource/" + urllib.parse.quote(doctype)
                + "?fields=[\"name\"]&filters=" + urllib.parse.quote(filters))
        out = self._request("GET", path)
        return bool(out.get("data"))

    def insert(self, doc: dict) -> str:
        doctype = doc.pop("doctype")
        out = self._request("POST", "/api/resource/" + urllib.parse.quote(doctype), doc)
        return out.get("data", {}).get("name", "")


# ------------------------------------------------------------- spool

@dataclass
class SpoolEntry:
    id: str
    kind: str          # stock | claim | procurement | ledger
    payload: dict      # kwargs for the kind's mapper


class Spool:
    """JSONL buffer. The whole pending set is rewritten atomically after
    each flush; ERPNext-side idempotency (openhealth_id) covers the
    crash-mid-flush window, so a rewritten-never-happened crash re-sends
    harmlessly."""

    def __init__(self, path: str):
        self.path = path

    def push(self, entry: SpoolEntry) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": entry.id, "kind": entry.kind,
                                "payload": entry.payload}) + "\n")

    def pending(self) -> list[SpoolEntry]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    d = json.loads(line)
                    out.append(SpoolEntry(d["id"], d["kind"], d.get("payload", {})))
        return out

    def rewrite(self, entries: list[SpoolEntry]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps({"id": e.id, "kind": e.kind,
                                    "payload": e.payload}) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)


# ------------------------------------------------------------- bridge

@dataclass
class FlushResult:
    sent: int = 0
    skipped_duplicates: int = 0
    failed: int = 0
    offline: bool = False


class ERPNextBridge:
    """Spool in, ERPNext out. The platform never blocks on the ERP.

    coa (optional): a coa.COAMapping. When set, Journal Entry account
    legs are rewritten platform-code → GL account at flush time, and an
    unmapped account code fails the entry loudly instead of leaking an
    operational code into the finance books."""

    def __init__(self, client: FrappeClient | None, spool: Spool, coa=None):
        self.client = client      # None = offline mode: buffer only
        self.spool = spool
        self.coa = coa

    def enqueue(self, kind: str, event_id: str, **payload) -> None:
        """Buffer one platform event. The payload kwargs are reassembled
        into the dict the kind's mapper expects — e.g.
        enqueue("claim", cid, claim=claim, decision=decision)."""
        if kind not in MAPPERS:
            raise ValueError(f"unknown bridge kind: {kind!r}")
        self.spool.push(SpoolEntry(event_id, kind, payload))

    def flush(self) -> FlushResult:
        result = FlushResult()
        pending = self.spool.pending()
        if self.client is None:
            result.offline = True
            result.failed = len(pending)
            return result
        remaining: list[SpoolEntry] = []
        for i, entry in enumerate(pending):
            try:
                doc = MAPPERS[entry.kind](entry.payload)
                if self.coa is not None and doc["doctype"] == "Journal Entry":
                    doc = self.coa.apply_to_journal(doc)
                if self.client.exists(doc["doctype"], entry.id):
                    result.skipped_duplicates += 1
                    continue
                self.client.insert(doc)
                result.sent += 1
            except FrappeOffline:
                # Link down: keep this entry and everything after it —
                # order preserved, no hammering a dead server.
                result.offline = True
                remaining.extend(pending[i:])
                break
            except (ValueError, KeyError):
                # A malformed event is dropped loudly so it cannot poison
                # the queue forever; the caller logs the failed count.
                result.failed += 1
        self.spool.rewrite(remaining)
        return result
