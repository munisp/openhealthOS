#!/usr/bin/env python3
"""COA mapping tests + stub-server end-to-end (ADR-0015).

The e2e proves the whole ERP path with a live HTTP stub: spool a ledger
batch → flush → exists-check → insert, with platform account codes
rewritten to GL accounts by the bundle — and remote validation catching
a bundle that names an account the finance office never created.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import coa
from erpnext_bridge import ERPNextBridge, FrappeClient, Spool

BUNDLE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "bundles", "coa-ng-default.json")


class TestBundle(unittest.TestCase):
    def test_default_bundle_loads(self):
        m = coa.load(BUNDLE)
        self.assertEqual(m.company, "OpenHealth Nigeria")
        self.assertEqual(m.suffix, "OHN")
        self.assertIn("SETTLE-POOL", m.accounts)

    def test_exact_and_prefix_mapping(self):
        m = coa.load(BUNDLE)
        self.assertEqual(m.map_account("SETTLE-POOL"), "Scheme Settlement Pool - OHN")
        self.assertEqual(m.map_account("WALLET-PHC-0042"), "Facility Wallets - OHN")
        self.assertEqual(m.map_account("PREMIUM-NHIA-2026"), "Premium Contributions - OHN")

    def test_unmapped_code_is_a_hard_error(self):
        m = coa.load(BUNDLE)
        with self.assertRaises(coa.UnmappedAccount):
            m.map_account("PETTY-CASH-DRAWER")

    def test_malformed_bundles_rejected(self):
        bad_cases = [
            {"suffix": "X", "accounts": {"A": "A - X"}},                    # no company
            {"company": "C", "accounts": {"A": "A - X"}},                   # no suffix
            {"company": "C", "suffix": "X", "accounts": {}},                # empty
            {"company": "C", "suffix": "X", "accounts": {"A": "Wrong Co"}},  # suffix mismatch
            {"company": "C", "suffix": "X", "accounts": {"A": ""}},          # empty target
        ]
        for i, raw in enumerate(bad_cases):
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(raw, f)
                path = f.name
            try:
                with self.assertRaises(coa.InvalidBundle, msg=f"case {i}"):
                    coa.load(path)
            finally:
                os.unlink(path)

    def test_journal_rewrite(self):
        m = coa.load(BUNDLE)
        doc = {"doctype": "Journal Entry",
               "accounts": [{"account": "SETTLE-POOL", "debit_in_account_currency": 100.0},
                            {"account": "WALLET-PHC-1", "credit_in_account_currency": 100.0}]}
        out = m.apply_to_journal(doc)
        self.assertEqual(out["accounts"][0]["account"], "Scheme Settlement Pool - OHN")
        self.assertEqual(out["accounts"][1]["account"], "Facility Wallets - OHN")


# ------------------------------------------------- stub Frappe with COA

class FakeCOAFrappe(BaseHTTPRequestHandler):
    """Frappe surface: Account list, openhealth_id exists-check, insert."""
    docs: list[dict] = []
    accounts: list[str] = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        q = parse_qs(parsed.query)
        if parsed.path == "/api/resource/Account":
            self._send({"data": [{"name": n} for n in FakeCOAFrappe.accounts]})
            return
        filters = json.loads(q.get("filters", ["[]"])[0])
        ohid = filters[0][2] if filters else ""
        found = [d for d in FakeCOAFrappe.docs if d.get("openhealth_id") == ohid]
        self._send({"data": [{"name": d["name"]} for d in found]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        doc = json.loads(self.rfile.read(length) or b"{}")
        doc["name"] = f"ERP-{len(FakeCOAFrappe.docs) + 1:04d}"
        FakeCOAFrappe.docs.append(doc)
        self._send({"data": {"name": doc["name"]}})

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fake():
    FakeCOAFrappe.docs = []
    FakeCOAFrappe.accounts = [
        "Scheme Settlement Pool - OHN", "PSP Float - OHN", "Debtors - OHN",
        "Sales - OHN", "Stock In Hand - OHN", "Creditors - OHN",
        "Facility Wallets - OHN", "Premium Contributions - OHN",
    ]
    srv = HTTPServer(("127.0.0.1", 0), FakeCOAFrappe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class TestEndToEnd(unittest.TestCase):
    def test_flush_maps_accounts_and_remote_validates(self):
        srv = start_fake()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            client = FrappeClient(base, key="k", secret="s")
            mapping = coa.load(BUNDLE)

            # Remote validation: the default bundle matches the stub's chart.
            self.assertEqual(coa.validate_remote(client, mapping), [])

            # Spool a settlement payout batch and flush with COA mapping.
            with tempfile.TemporaryDirectory() as td:
                bridge = ERPNextBridge(client, Spool(os.path.join(td, "spool.jsonl")), coa=mapping)
                batch = {"id": "SETTLE-0001", "narration": "NHIA settlement run 2026-W42",
                         "legs": [
                             {"account": "SETTLE-POOL", "side": "debit", "amount_kobo": 250_000_00},
                             {"account": "WALLET-PHC-0042", "side": "credit", "amount_kobo": 250_000_00},
                         ]}
                bridge.enqueue("ledger", "SETTLE-0001", **batch)
                res = bridge.flush()
                self.assertEqual(res.sent, 1)
                self.assertEqual(res.failed, 0)

            posted = FakeCOAFrappe.docs[0]  # doctype is the URL, not the body
            self.assertEqual(posted["voucher_type"], "Journal Entry")
            accounts = {leg["account"] for leg in posted["accounts"]}
            self.assertEqual(accounts, {"Scheme Settlement Pool - OHN", "Facility Wallets - OHN"})
            # No platform code leaked into the books.
            self.assertNotIn("SETTLE-POOL", json.dumps(posted["accounts"]))

            # Idempotency still holds with mapping in play.
            with tempfile.TemporaryDirectory() as td:
                bridge = ERPNextBridge(client, Spool(os.path.join(td, "spool.jsonl")), coa=mapping)
                bridge.enqueue("ledger", "SETTLE-0001", **batch)
                res = bridge.flush()
                self.assertEqual(res.skipped_duplicates, 1)
                self.assertEqual(len(FakeCOAFrappe.docs), 1)

            # An unmapped account fails loudly and does not post.
            with tempfile.TemporaryDirectory() as td:
                bridge = ERPNextBridge(client, Spool(os.path.join(td, "spool.jsonl")), coa=mapping)
                bad = {"id": "SETTLE-0002",
                       "legs": [{"account": "MYSTERY", "side": "debit", "amount_kobo": 100},
                                {"account": "SETTLE-POOL", "side": "credit", "amount_kobo": 100}]}
                bridge.enqueue("ledger", "SETTLE-0002", **bad)
                res = bridge.flush()
                self.assertEqual(res.failed, 1)
                self.assertEqual(len(FakeCOAFrappe.docs), 1)
        finally:
            srv.shutdown()

    def test_remote_validation_catches_missing_gl_account(self):
        srv = start_fake()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            client = FrappeClient(base)
            FakeCOAFrappe.accounts.remove("PSP Float - OHN")  # finance office deleted it
            missing = coa.validate_remote(client, coa.load(BUNDLE))
            self.assertEqual(missing, ["PSP Float - OHN"])
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
