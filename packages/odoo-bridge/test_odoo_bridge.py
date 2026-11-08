#!/usr/bin/env python3
"""Odoo bridge tests: mapper shapes, offline spool, idempotent flush,
order preservation — against a fake Odoo speaking real XML-RPC on a real
socket (no Odoo needed), plus injected proxies for failure injection."""
import os
import tempfile
import threading
import unittest
import xmlrpc.client
from xmlrpc.server import SimpleXMLRPCRequestHandler, SimpleXMLRPCServer

from odoo_bridge import (MAPPERS, OdooAuthError, OdooBridge, OdooClient,
                         OdooOffline, Spool, customer_invoice, journal_entry,
                         purchase_order, stock_picking)


class OdooHandler(SimpleXMLRPCRequestHandler):
    rpc_paths = ("/xmlrpc/2/common", "/xmlrpc/2/object")


class FakeOdoo:
    """Minimal Odoo surface: authenticate + execute_kw(search_count/create/
    name_search). Records keyed by model; create assigns numeric ids."""

    def __init__(self):
        self.records: dict[str, list[dict]] = {}
        self.next_id = 1
        self.created_order: list[str] = []   # x_openhealth_id in create order
        self.fail_creates_from: int | None = None  # wire-fault injection point

    def authenticate(self, db, login, password, ctx):
        return 7 if (db, login, password) == ("oh", "admin", "pw") else False

    def execute_kw(self, db, uid, password, model, method, args, kwargs=None):
        assert uid == 7, "unauthenticated execute_kw"
        if method == "search_count":
            domain = args[0]
            ohid = domain[0][2] if domain else ""
            return sum(1 for r in self.records.get(model, [])
                       if r.get("x_openhealth_id") == ohid)
        if method == "create":
            if (self.fail_creates_from is not None
                    and self.next_id >= self.fail_creates_from):
                raise ConnectionRefusedError("link down")   # → OdooOffline
            vals = dict(args[0])
            vals["id"] = self.next_id
            self.next_id += 1
            self.records.setdefault(model, []).append(vals)
            self.created_order.append(vals.get("x_openhealth_id", ""))
            return vals["id"]
        if method == "name_search":
            if model == "res.partner" and args[0] == "NHIA":
                return [(42, "NHIA")]
            return []
        raise AssertionError(f"unexpected method {method}")


def start_fake_odoo(fake: FakeOdoo):
    srv = SimpleXMLRPCServer(("127.0.0.1", 0), requestHandler=OdooHandler,
                             logRequests=False, allow_none=True)
    srv.register_function(fake.authenticate, "authenticate")
    srv.register_function(fake.execute_kw, "execute_kw")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


CLAIM = {"claim_id": "CLM-1", "facility_id": "PHC-1",
         "lines": [{"code": "MAL-RDT", "qty": 1, "unit_price_ngn": 1500}]}
APPROVED = {"decision": "APPROVE", "scheme": "BHCPF-VUL"}
LEGS_BALANCED = [
    {"account": "NHIA Receivable - OH", "side": "debit", "amount_kobo": 288000},
    {"account": "Service Revenue - OH", "side": "credit", "amount_kobo": 288000}]


class TestMappers(unittest.TestCase):
    def test_stock_picking_incoming_outgoing(self):
        r = stock_picking({"id": "STK-1", "movement": "receive", "sku": "AL8020",
                           "qty": 100, "facility": "PHC-1", "date": "2026-11-08"})
        self.assertEqual(r["model"], "stock.picking")
        self.assertEqual(r["vals"]["x_picking_kind"], "incoming")
        self.assertEqual(r["vals"]["x_openhealth_id"], "STK-1")
        self.assertEqual(r["vals"]["x_lines"], [{"sku": "AL8020", "qty": 100}])
        d = stock_picking({"id": "STK-2", "movement": "dispense", "sku": "AL8020", "qty": 24})
        self.assertEqual(d["vals"]["x_picking_kind"], "outgoing")
        with self.assertRaises(ValueError):
            stock_picking({"id": "X", "movement": "teleport", "sku": "S", "qty": 1})

    def test_customer_invoice_requires_approval(self):
        inv = customer_invoice({"claim": CLAIM, "decision": APPROVED})
        self.assertEqual(inv["model"], "account.move")
        self.assertEqual(inv["vals"]["move_type"], "out_invoice")
        self.assertEqual(inv["vals"]["x_partner_ref"], "BHCPF-VUL")
        self.assertEqual(inv["vals"]["x_lines"][0]["unit_price"], 1500)
        with self.assertRaises(ValueError):
            customer_invoice({"claim": CLAIM, "decision": {"decision": "PEND"}})

    def test_purchase_order(self):
        po = purchase_order({"id": "PO-1", "supplier": "MedSupply Ltd",
                             "items": {"AL8020": 500}, "rates": {"AL8020": 80}})
        self.assertEqual(po["model"], "purchase.order")
        self.assertEqual(po["vals"]["x_supplier_ref"], "MedSupply Ltd")
        self.assertEqual(po["vals"]["x_lines"][0]["qty"], 500)

    def test_journal_entry_must_balance(self):
        je = journal_entry({"id": "JE-1", "legs": LEGS_BALANCED})
        self.assertEqual(je["vals"]["move_type"], "entry")
        self.assertEqual(je["vals"]["x_legs"][0]["debit_minor"], 288000)
        self.assertEqual(je["vals"]["x_legs"][1]["credit_minor"], 288000)
        bad = {"id": "JE-2", "legs": [
            {"account": "A", "side": "debit", "amount_kobo": 100},
            {"account": "B", "side": "credit", "amount_kobo": 50}]}
        with self.assertRaises(ValueError):
            journal_entry(bad)


class TestClient(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOdoo()
        self.srv = start_fake_odoo(self.fake)
        self.client = OdooClient(f"http://127.0.0.1:{self.srv.server_address[1]}",
                                 db="oh", login="admin", password="pw")

    def tearDown(self):
        self.srv.shutdown()

    def test_auth_and_create_over_real_xmlrpc(self):
        rid = self.client.create("stock.picking",
                                 {"x_openhealth_id": "STK-1", "origin": "STK-1"})
        self.assertEqual(rid, 1)
        self.assertTrue(self.client.exists("stock.picking", "STK-1"))
        self.assertFalse(self.client.exists("stock.picking", "STK-2"))

    def test_auth_refused_is_stated_not_retried(self):
        bad = OdooClient(f"http://127.0.0.1:{self.srv.server_address[1]}",
                         db="oh", login="admin", password="wrong")
        with self.assertRaises(OdooAuthError):
            bad.uid()

    def test_dead_server_is_offline(self):
        self.srv.shutdown()
        dead = OdooClient(f"http://127.0.0.1:{self.srv.server_address[1]}",
                          db="oh", login="admin", password="pw", timeout=2.0)
        with self.assertRaises(OdooOffline):
            dead.uid()
        self.srv.serve_forever  # keep reference; restart not needed

    def test_resolve_name_search(self):
        self.assertEqual(self.client.resolve("res.partner", "NHIA"), 42)
        with self.assertRaises(ValueError):
            self.client.resolve("res.partner", "NOBODY")


class FlakyProxies:
    """Injected proxy factory: runs against the FakeOdoo state machine
    but raises link errors at the *client* level (a real server converts
    handler exceptions into Faults — a dead link looks different)."""

    def __init__(self, fake: FakeOdoo):
        self.fake = fake

    def __call__(self, path):
        fake = self.fake

        class P:
            def authenticate(self, *a):
                return fake.authenticate(*a)

            def execute_kw(self, *a, **kw):
                return fake.execute_kw(*a, **kw)

        return P()


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.spool = Spool(os.path.join(self.dir, "odoo.spool"))
        self.fake = FakeOdoo()
        self.srv = start_fake_odoo(self.fake)
        self.client = OdooClient(f"http://127.0.0.1:{self.srv.server_address[1]}",
                                 db="oh", login="admin", password="pw")

    def tearDown(self):
        self.srv.shutdown()

    def _enqueue_pair(self, bridge):
        bridge.enqueue("stock", "STK-1", id="STK-1", movement="receive",
                       sku="AL8020", qty=100)
        bridge.enqueue("claim", "CLM-1", claim=CLAIM, decision=APPROVED)

    def test_offline_buffers_then_flush_sends(self):
        bridge = OdooBridge(None, self.spool)
        self._enqueue_pair(bridge)
        res = bridge.flush()
        self.assertTrue(res.offline)
        self.assertEqual(len(self.spool.pending()), 2)

        bridge = OdooBridge(self.client, self.spool)
        res = bridge.flush()
        self.assertEqual((res.sent, res.offline), (2, False))
        self.assertEqual(self.spool.pending(), [])
        self.assertEqual(len(self.fake.records["stock.picking"]), 1)
        self.assertEqual(len(self.fake.records["account.move"]), 1)

        # Replay: idempotent, zero double-posts — the books never see a
        # claim twice.
        bridge.enqueue("stock", "STK-1", id="STK-1", movement="receive",
                       sku="AL8020", qty=100)
        res = bridge.flush()
        self.assertEqual(res.skipped_duplicates, 1)
        self.assertEqual(len(self.fake.records["stock.picking"]), 1)

    def test_offline_mid_flush_preserves_order(self):
        flaky = OdooClient("http://injected", db="oh", login="admin",
                           password="pw", proxy_factory=FlakyProxies(self.fake))
        bridge = OdooBridge(flaky, self.spool)
        for n in range(5):
            bridge.enqueue("stock", f"STK-{n}", id=f"STK-{n}",
                           movement="receive", sku="AL8020", qty=n + 1)
        # Link dies after two creates (search_count passes, create fails).
        self.fake.fail_creates_from = 3
        res = bridge.flush()
        self.assertTrue(res.offline)
        self.assertEqual(res.sent, 2)
        self.assertEqual([e.id for e in self.spool.pending()],
                         ["STK-2", "STK-3", "STK-4"])

        # Link returns: the rest flush in original order.
        self.fake.fail_creates_from = None
        res = bridge.flush()
        self.assertEqual(res.sent, 3)
        self.assertEqual(self.fake.created_order,
                         ["STK-0", "STK-1", "STK-2", "STK-3", "STK-4"])

    def test_malformed_dropped_loudly_good_one_posts(self):
        bridge = OdooBridge(self.client, self.spool)
        bridge.enqueue("stock", "BAD-1", id="BAD-1", movement="teleport", sku="X", qty=1)
        bridge.enqueue("stock", "STK-9", id="STK-9", movement="receive", sku="AL8020", qty=5)
        res = bridge.flush()
        self.assertEqual((res.failed, res.sent), (1, 1))
        self.assertEqual(self.spool.pending(), [])

    def test_odoo_fault_dropped_loudly_not_queue_poison(self):
        class FaultyProxies:
            def __call__(self, path):
                class P:
                    def authenticate(self, *a):
                        return 7

                    def execute_kw(self, db, uid, pw, model, method, args, kwargs=None):
                        if method == "search_count":
                            return 0
                        raise xmlrpc.client.Fault(1, "validation: bad picking type")
                return P()

        client = OdooClient("http://unused", db="oh", login="a", password="b",
                            proxy_factory=FaultyProxies())
        bridge = OdooBridge(client, self.spool)
        bridge.enqueue("stock", "STK-1", id="STK-1", movement="receive", sku="S", qty=1)
        res = bridge.flush()
        self.assertEqual(res.failed, 1)
        self.assertFalse(res.offline)          # a rejection is not a link failure
        self.assertEqual(self.spool.pending(), [])

    def test_unknown_kind_refused_at_enqueue(self):
        bridge = OdooBridge(None, self.spool)
        with self.assertRaises(ValueError):
            bridge.enqueue("astrology", "X-1", id="X-1")
        self.assertEqual(sorted(MAPPERS), ["claim", "ledger", "procurement", "stock"])

    def test_unbalanced_ledger_batch_never_posts(self):
        bridge = OdooBridge(self.client, self.spool)
        bridge.enqueue("ledger", "JE-BAD", id="JE-BAD", legs=[
            {"account": "A", "side": "debit", "amount_kobo": 100},
            {"account": "B", "side": "credit", "amount_kobo": 99}])
        res = bridge.flush()
        self.assertEqual(res.failed, 1)
        self.assertEqual(self.fake.records.get("account.move", []), [])

    def test_coa_rewrite_and_unmapped_refusal(self):
        from coa import COAMapping, UnmappedAccount
        coa = COAMapping(company="OHN", suffix="OHN", accounts={
            "NHIA-RECV": "NHIA Receivable - OHN",
            "SVC-REV": "Service Revenue - OHN"})
        bridge = OdooBridge(self.client, self.spool, coa=coa)
        bridge.enqueue("ledger", "JE-1", id="JE-1", legs=[
            {"account": "NHIA-RECV", "side": "debit", "amount_kobo": 500},
            {"account": "SVC-REV", "side": "credit", "amount_kobo": 500}])
        res = bridge.flush()
        self.assertEqual(res.sent, 1)
        legs = self.fake.records["account.move"][0]["x_legs"]
        self.assertEqual(legs[0]["account"], "NHIA Receivable - OHN")

        # An unmapped code fails loudly; the platform code never leaks
        # into the finance books.
        self.assertIs(UnmappedAccount.__mro__[1], ValueError)
        bridge.enqueue("ledger", "JE-2", id="JE-2", legs=[
            {"account": "PLATFORM-ONLY", "side": "debit", "amount_kobo": 5},
            {"account": "SVC-REV", "side": "credit", "amount_kobo": 5}])
        res = bridge.flush()
        self.assertEqual(res.failed, 1)
        self.assertEqual(len(self.fake.records["account.move"]), 1)


if __name__ == "__main__":
    unittest.main()
