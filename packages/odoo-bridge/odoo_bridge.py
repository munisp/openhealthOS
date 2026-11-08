#!/usr/bin/env python3
"""Odoo bridge — accounting, inventory and procurement sync (ADR-0012).

Odoo Community (LGPLv3) is composed over its XML-RPC API, never embedded
(ADR-0005): the platform's ledger, pharmacy stock and procurement stays the
system of record; Odoo is the finance office's book — general ledger,
warehouse stock, supplier orders. The network boundary keeps the licences
independent, and the platform keeps working when Odoo is down: everything
buffers in a JSONL spool and flushes when the link returns.

This is the Odoo twin of the ERPNext bridge — same doctrines, different
wire. Three differences are real and stated, not smoothed over:

  1. Wire protocol: Odoo speaks XML-RPC (stdlib xmlrpc.client), not REST.
     Authenticate once against /xmlrpc/2/common, then execute_kw against
     /xmlrpc/2/object.
  2. Identity model: Odoo keys records by numeric id, not by name.
     Platform references therefore cross as structured `x_openhealth_*`
     custom fields (XML-RPC carries nested structs natively); the Odoo
     side's automation resolves them to products, partners, warehouses.
     No numeric Odoo id is ever invented offline.
  3. Idempotency anchor: `x_openhealth_id` (a custom field on the Odoo
     side); the bridge search_count-before-create, so a crash mid-flush
     and a replayed event can never double-post to the books.

What flows where:
  * pharmacy stock receive/dispense  → stock.picking (incoming/outgoing)
  * APPROVEd claim                   → account.move (out_invoice; the payer
                                       is the customer)
  * pooled procurement order         → purchase.order
  * ledger double-entry batch        → account.move (entry)

Failure taxonomy (stated, fail-closed):
  * link/server down (OSError, TimeoutError, HTTP ProtocolError)
      → OdooOffline: buffer; flush keeps this entry and everything after
        it — order preserved, no hammering a dead server.
  * Odoo-side rejection (xmlrpc.client.Fault) or a malformed event
    (ValueError, KeyError) or an unmapped COA account
      → dropped loudly and counted: a poisoned entry never blocks the
        queue forever, and it never posts.
"""
from __future__ import annotations

import json
import os
import socket
import xmlrpc.client
from dataclasses import dataclass

VERSION = "odoo_bridge@1.0.0"


# ------------------------------------------------------------- mappers
# Pure functions: platform event → {"model": ..., "vals": ...}.
# Deterministic and unit-tested without any server.

def stock_picking(ev: dict) -> dict:
    """Pharmacy stock movement → stock.picking."""
    movement = ev.get("movement", "")   # receive | dispense
    kind = {"receive": "incoming", "dispense": "outgoing"}.get(movement)
    if kind is None:
        raise ValueError(f"unknown stock movement: {movement!r}")
    return {
        "model": "stock.picking",
        "vals": {
            "x_openhealth_id": ev["id"],
            "origin": ev["id"],
            "x_picking_kind": kind,
            "x_facility": ev.get("facility", ""),
            "x_lines": [{"sku": ev["sku"], "qty": ev["qty"]}],
            "x_date": ev.get("date", ""),
        },
    }


def customer_invoice(ev: dict) -> dict:
    """APPROVEd claim → account.move out_invoice; the payer is the customer.
    ev = {"claim": ..., "decision": ..., "customer": optional override}."""
    claim, decision = ev["claim"], ev["decision"]
    if decision.get("decision") != "APPROVE":
        raise ValueError("only APPROVEd claims are invoiced")
    return {
        "model": "account.move",
        "vals": {
            "move_type": "out_invoice",
            "x_openhealth_id": claim["claim_id"],
            "x_partner_ref": ev.get("customer") or decision.get("scheme", ""),
            "x_facility": claim.get("facility_id", ""),
            "x_lines": [{"code": ln["code"], "qty": ln.get("qty", 1),
                         "unit_price": ln.get("unit_price_ngn", 0)}
                        for ln in claim.get("lines", [])],
            "x_narration": f"payer claim {claim['claim_id']} — "
                           f"facility {claim.get('facility_id', '')}",
        },
    }


def purchase_order(order: dict) -> dict:
    """Pooled procurement order → purchase.order."""
    return {
        "model": "purchase.order",
        "vals": {
            "x_openhealth_id": order["id"],
            "origin": order["id"],
            "x_supplier_ref": order["supplier"],
            "x_lines": [{"sku": sku, "qty": qty,
                         "unit_price": order.get("rates", {}).get(sku, 0)}
                        for sku, qty in order.get("items", {}).items()],
        },
    }


def journal_entry(batch: dict) -> dict:
    """Ledger double-entry batch → account.move entry. Debits must equal
    credits — the Rust ledger already enforces this; the mapper asserts
    it again. Amounts are in minor units (kobo/centavos); the Odoo side
    divides by the currency scale on import."""
    legs = []
    debit = credit = 0
    for leg in batch.get("legs", []):
        amt = int(leg["amount_kobo"])
        if leg["side"] == "debit":
            legs.append({"account": leg["account"], "debit_minor": amt, "credit_minor": 0})
            debit += amt
        else:
            legs.append({"account": leg["account"], "debit_minor": 0, "credit_minor": amt})
            credit += amt
    if debit != credit:
        raise ValueError(f"unbalanced batch: debits {debit} != credits {credit}")
    return {
        "model": "account.move",
        "vals": {
            "move_type": "entry",
            "x_openhealth_id": batch["id"],
            "x_legs": legs,
            "x_narration": batch.get("narration", "OpenHealth ledger batch"),
        },
    }


MAPPERS = {
    "stock": stock_picking,
    "claim": customer_invoice,
    "procurement": purchase_order,
    "ledger": journal_entry,
}


# ------------------------------------------------------------- client

class OdooOffline(IOError):
    """Odoo unreachable (link or server) — the caller buffers and retries."""


class OdooAuthError(PermissionError):
    """Authentication refused — bad credentials are a config fault,
    never retried silently."""


class _TimeoutTransport(xmlrpc.client.Transport):
    """xmlrpc.client.Transport with a real socket timeout (the stdlib
    transport has none — a hung server would otherwise hang the flush)."""

    def __init__(self, timeout: float, **kw):
        super().__init__(**kw)
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class OdooClient:
    """Minimal Odoo XML-RPC client (stdlib only).

    proxy_factory is injectable for tests: path ("common" | "object")
    → object with .authenticate(...) / .execute_kw(...). Default builds
    a real ServerProxy over a timeout transport."""

    def __init__(self, url: str, db: str, login: str = "", password: str = "",
                 timeout: float = 10.0, proxy_factory=None):
        self.url = url.rstrip("/")
        self.db = db
        self.login = login
        self.password = password
        self.timeout = timeout
        if proxy_factory is None:
            transport = _TimeoutTransport(timeout)
            proxy_factory = lambda path: xmlrpc.client.ServerProxy(  # noqa: E731
                self.url + "/xmlrpc/2/" + path, transport=transport)
        self._proxy_factory = proxy_factory
        self._proxies: dict[str, object] = {}
        self._uid: int | None = None

    def _proxy(self, path: str):
        if path not in self._proxies:
            self._proxies[path] = self._proxy_factory(path)
        return self._proxies[path]

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except xmlrpc.client.Fault:
            raise          # application-level rejection — the bridge counts it
        except (OSError, TimeoutError, socket.timeout,
                xmlrpc.client.ProtocolError) as e:
            raise OdooOffline(str(e)) from e

    def uid(self) -> int:
        """Authenticate once, cache the uid. Refusal is stated, never
        retried behind the caller's back."""
        if self._uid is None:
            uid = self._call(self._proxy("common").authenticate,
                             self.db, self.login, self.password, {})
            if not uid:
                raise OdooAuthError(f"odoo authentication refused for {self.login!r}")
            self._uid = int(uid)
        return self._uid

    def execute(self, model: str, method: str, args: list, kwargs: dict | None = None):
        return self._call(self._proxy("object").execute_kw,
                          self.db, self.uid(), self.password,
                          model, method, list(args), kwargs or {})

    def exists(self, model: str, openhealth_id: str) -> bool:
        """Idempotency anchor: has this platform event been posted already?"""
        return bool(self.execute(model, "search_count",
                                 [[["x_openhealth_id", "=", openhealth_id]]]))

    def create(self, model: str, vals: dict) -> int:
        return int(self.execute(model, "create", [vals]))

    def resolve(self, model: str, name: str) -> int:
        """Name → numeric id for deployment-side references (partners,
        suppliers). Unknown names are stated, not guessed."""
        hits = self.execute(model, "name_search", [name], {"limit": 1})
        if not hits:
            raise ValueError(f"no {model} named {name!r} on the Odoo side")
        return int(hits[0][0])


# ------------------------------------------------------------- spool
# Identical doctrine to the ERPNext bridge: JSONL buffer, whole pending
# set rewritten atomically after each flush; server-side idempotency
# (x_openhealth_id) covers the crash-mid-flush window, so a
# rewritten-never-happened crash re-sends harmlessly.

@dataclass
class SpoolEntry:
    id: str
    kind: str          # stock | claim | procurement | ledger
    payload: dict      # kwargs for the kind's mapper


class Spool:
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


class OdooBridge:
    """Spool in, Odoo out. The platform never blocks on the ERP.

    coa (optional): a COAMapping (packages/erpnext-bridge/coa.py — the
    doctrine is shared). When set, ledger batch leg accounts are rewritten
    platform-code → GL account at flush time, and an unmapped account code
    fails the entry loudly instead of leaking an operational code into
    the finance books."""

    def __init__(self, client: OdooClient | None, spool: Spool, coa=None):
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
                if (self.coa is not None and doc["model"] == "account.move"
                        and doc["vals"].get("move_type") == "entry"):
                    for leg in doc["vals"]["x_legs"]:
                        leg["account"] = self.coa.map_account(leg["account"])
                if self.client.exists(doc["model"], entry.id):
                    result.skipped_duplicates += 1
                    continue
                self.client.create(doc["model"], doc["vals"])
                result.sent += 1
            except OdooOffline:
                # Link down: keep this entry and everything after it —
                # order preserved, no hammering a dead server.
                result.offline = True
                remaining.extend(pending[i:])
                break
            except (ValueError, KeyError, xmlrpc.client.Fault):
                # A malformed or rejected event is dropped loudly so it
                # cannot poison the queue forever; the caller logs the
                # failed count. It never posts.
                result.failed += 1
        self.spool.rewrite(remaining)
        return result
