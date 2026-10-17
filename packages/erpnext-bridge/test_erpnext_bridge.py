#!/usr/bin/env python3
"""ERPNext bridge tests: mapper shapes, offline spool, idempotent flush —
against a fake Frappe server, no network, no ERPNext needed."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from erpnext_bridge import (ERPNextBridge, FrappeClient, Spool,
                            journal_entry, purchase_order, sales_invoice,
                            stock_entry)


class FakeFrappe(BaseHTTPRequestHandler):
    """Minimal Frappe REST surface: GET exists-check, POST insert."""
    docs: list[dict] = []

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        filters = json.loads(q.get("filters", ["[]"])[0])
        ohid = filters[0][2] if filters else ""
        found = [d for d in FakeFrappe.docs if d.get("openhealth_id") == ohid]
        self._send({"data": [{"name": d["name"]} for d in found]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        doc = json.loads(self.rfile.read(length) or b"{}")
        doc["name"] = f"ERP-{len(FakeFrappe.docs) + 1:04d}"
        FakeFrappe.docs.append(doc)
        self._send({"data": {"name": doc["name"]}})

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fake_frappe():
    FakeFrappe.docs = []
    srv = HTTPServer(("127.0.0.1", 0), FakeFrappe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestMappers(unittest.TestCase):
    def test_stock_receive_and_issue(self):
        r = stock_entry({"id": "STK-1", "movement": "receive", "sku": "AL8020", "qty": 100,
                         "facility": "PHC-1", "date": "2026-10-17"})
        self.assertEqual(r["stock_entry_type"], "Material Receipt")
        self.assertEqual(r["items"][0]["t_warehouse"], "Stores - PHC-1")
        self.assertEqual(r["openhealth_id"], "STK-1")
        d = stock_entry({"id": "STK-2", "movement": "dispense", "sku": "AL8020", "qty": 24})
        self.assertEqual(d["stock_entry_type"], "Material Issue")
        with self.assertRaises(ValueError):
            stock_entry({"id": "X", "movement": "teleport", "sku": "S", "qty": 1})

    def test_sales_invoice_requires_approval(self):
        cl = {"claim_id": "CLM-1", "facility_id": "PHC-1",
              "lines": [{"code": "MAL-RDT", "qty": 1, "unit_price_ngn": 1500}]}
        inv = sales_invoice({"claim": cl, "decision": {"decision": "APPROVE", "scheme": "BHCPF-VUL"}})
        self.assertEqual(inv["customer"], "BHCPF-VUL")
        self.assertEqual(inv["openhealth_id"], "CLM-1")
        self.assertEqual(inv["items"][0]["rate"], 1500)
        with self.assertRaises(ValueError):
            sales_invoice({"claim": cl, "decision": {"decision": "PEND", "scheme": "X"}})

    def test_purchase_order(self):
        po = purchase_order({"id": "PO-1", "supplier": "MedSupply Ltd",
                             "items": {"AL8020": 500}, "rates": {"AL8020": 80}})
        self.assertEqual(po["supplier"], "MedSupply Ltd")
        self.assertEqual(po["items"][0]["qty"], 500)

    def test_journal_entry_must_balance(self):
        good = {"id": "JE-1", "legs": [
            {"account": "NHIA Receivable - OH", "side": "debit", "amount_kobo": 288000},
            {"account": "Service Revenue - OH", "side": "credit", "amount_kobo": 288000}]}
        je = journal_entry(good)
        self.assertEqual(je["accounts"][0]["debit_in_account_currency"], 2880.0)
        bad = {"id": "JE-2", "legs": [
            {"account": "A", "side": "debit", "amount_kobo": 100},
            {"account": "B", "side": "credit", "amount_kobo": 50}]}
        with self.assertRaises(ValueError):
            journal_entry(bad)


class TestBridge(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.spool = Spool(os.path.join(self.dir, "erp.spool"))

    def test_offline_buffers_then_flush_sends(self):
        # Offline: no client — everything buffers.
        bridge = ERPNextBridge(None, self.spool)
        bridge.enqueue("stock", "STK-1", id="STK-1", movement="receive", sku="AL8020", qty=100)
        bridge.enqueue("claim", "CLM-1",
                       claim={"claim_id": "CLM-1", "facility_id": "PHC-1",
                              "lines": [{"code": "MAL-RDT", "qty": 1, "unit_price_ngn": 1500}]},
                       decision={"decision": "APPROVE", "scheme": "BHCPF-VUL"})
        res = bridge.flush()
        self.assertTrue(res.offline)
        self.assertEqual(len(self.spool.pending()), 2)

        # Link returns: both post to the fake ERPNext.
        srv = start_fake_frappe()
        try:
            bridge = ERPNextBridge(
                FrappeClient(f"http://127.0.0.1:{srv.server_port}"), self.spool)
            res = bridge.flush()
            self.assertEqual(res.sent, 2)
            self.assertEqual(self.spool.pending(), [])
            self.assertEqual(len(FakeFrappe.docs), 2)

            # Re-flush after replaying the same events: idempotent, zero
            # double-posts — the books never see a claim twice.
            bridge.enqueue("stock", "STK-1", id="STK-1", movement="receive", sku="AL8020", qty=100)
            res = bridge.flush()
            self.assertEqual(res.skipped_duplicates, 1)
            self.assertEqual(len(FakeFrappe.docs), 2)
        finally:
            srv.shutdown()

    def test_malformed_event_dropped_loudly_not_queue_poison(self):
        srv = start_fake_frappe()
        try:
            bridge = ERPNextBridge(
                FrappeClient(f"http://127.0.0.1:{srv.server_port}"), self.spool)
            bridge.enqueue("stock", "BAD-1", id="BAD-1", movement="teleport", sku="X", qty=1)
            bridge.enqueue("stock", "STK-9", id="STK-9", movement="receive", sku="AL8020", qty=5)
            res = bridge.flush()
            self.assertEqual(res.failed, 1)
            self.assertEqual(res.sent, 1)  # the good one still posts
            self.assertEqual(self.spool.pending(), [])
        finally:
            srv.shutdown()

    def test_unknown_kind_refused_at_enqueue(self):
        bridge = ERPNextBridge(None, self.spool)
        with self.assertRaises(ValueError):
            bridge.enqueue("astrology", "X-1", id="X-1")

    def test_unbalanced_ledger_batch_never_posts(self):
        srv = start_fake_frappe()
        try:
            bridge = ERPNextBridge(
                FrappeClient(f"http://127.0.0.1:{srv.server_port}"), self.spool)
            bridge.enqueue("ledger", "JE-BAD", id="JE-BAD", legs=[
                {"account": "A", "side": "debit", "amount_kobo": 100},
                {"account": "B", "side": "credit", "amount_kobo": 99}])
            res = bridge.flush()
            self.assertEqual(res.failed, 1)
            self.assertEqual(len(FakeFrappe.docs), 0)  # nothing unbalanced in the books
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
