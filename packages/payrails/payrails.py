#!/usr/bin/env python3
"""Payment rails seam — Mojaloop-pattern switch and NIBSS NIP
(ADR-0013, innovation).

Settlement must survive Nigeria's reality: some schemes settle through a
Mojaloop-pattern hub, others through NIBSS Instant Payments (NIP), and
connectivity drops mid-transfer. Rules enforced here:

  * one tx_id settles on exactly ONE rail — cross-rail replay is refused
    (the payout equivalent of double-dipping across payers)
  * retries are idempotent per rail: same tx_id → same reference
  * sandbox mode is deterministic and offline; live mode speaks HTTP:
    Mojaloop FSPIOP /quotes → /transfers (ILP-style packet, matching
    services/payment-switch-mock), NIP name-enquiry → funds-transfer

The Go services keep their own HTTPSwitch seam; this package gives Python
jobs (payout batches, reconcilers, tests) the same rails.
"""
from __future__ import annotations

import base64
import json
import urllib.request
from dataclasses import dataclass


class CrossRailReplay(ValueError):
    """tx_id already settled on a different rail — refused."""


@dataclass
class PaymentRequest:
    tx_id: str
    payee: str                 # party identifier (device/account)
    amount_ngn: int
    narration: str = ""
    bank_code: str = ""        # NIP beneficiary bank
    account: str = ""          # NIP beneficiary account


@dataclass
class PaymentResult:
    tx_id: str
    rail: str
    reference: str
    status: str                # COMPLETED | DUPLICATE | ERROR
    detail: str = ""


class _Rail:
    name = "rail"

    def __init__(self, base_url: str = "", sandbox: bool = True):
        self.base_url = base_url.rstrip("/")
        self.sandbox = sandbox
        self._sent: dict[str, PaymentResult] = {}
        self._seq = 0

    def _dedupe(self, tx_id: str) -> PaymentResult | None:
        prior = self._sent.get(tx_id)
        if prior:
            return PaymentResult(tx_id, self.name, prior.reference, "DUPLICATE")
        return None

    def _record(self, tx_id: str, reference: str, status: str = "COMPLETED") -> PaymentResult:
        rec = PaymentResult(tx_id, self.name, reference, status)
        self._sent[tx_id] = rec
        return rec

    def _post(self, path: str, body: dict) -> dict:
        raw = json.dumps(body).encode()
        req = urllib.request.Request(self.base_url + path, data=raw,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())


class MojaloopRail(_Rail):
    """FSPIOP quote → transfer, ILP-style packet (payment-switch-mock shape)."""
    name = "mojaloop"

    def transfer(self, req: PaymentRequest) -> PaymentResult:
        if dup := self._dedupe(req.tx_id):
            return dup
        if self.sandbox:
            self._seq += 1
            return self._record(req.tx_id, f"MLP-SBX-{self._seq:06d}")
        quote = {"quoteId": f"Q-{req.tx_id}", "transactionId": req.tx_id,
                 "payee": {"partyIdType": "DEVICE", "partyIdentifier": req.payee},
                 "amountType": "SEND", "amount": str(req.amount_ngn), "currency": "NGN"}
        q = self._post("/quotes", quote)
        pkt = base64.urlsafe_b64encode(json.dumps(
            {"quoteId": q.get("quoteId", quote["quoteId"]),
             "amount": quote["amount"], "currency": "NGN"}).encode()).decode().rstrip("=")
        tr = self._post("/transfers", {"transferId": f"TR-{req.tx_id}",
                                       "quoteId": q.get("quoteId"),
                                       "ilpPacket": pkt, "condition": "prototype-condition-hash"})
        return self._record(req.tx_id, tr.get("transferId", ""),
                            tr.get("status", "COMPLETED"))


class NIPRail(_Rail):
    """NIBSS Instant Payments: name-enquiry then funds transfer."""
    name = "nip"

    def transfer(self, req: PaymentRequest) -> PaymentResult:
        if dup := self._dedupe(req.tx_id):
            return dup
        if not req.bank_code or not req.account:
            return PaymentResult(req.tx_id, self.name, "", "ERROR",
                                 "NIP requires bank_code and account")
        if self.sandbox:
            self._seq += 1
            return self._record(req.tx_id, f"NIP-SBX-{self._seq:06d}")
        ne = self._post("/name-enquiry", {"bankCode": req.bank_code, "account": req.account})
        ft = self._post("/transfers", {
            "sessionId": f"NIP-{req.tx_id}",
            "nameEnquiryRef": ne.get("nameEnquiryRef", ""),
            "beneficiaryBank": req.bank_code, "beneficiaryAccount": req.account,
            "amount": req.amount_ngn, "currency": "NGN", "narration": req.narration})
        return self._record(req.tx_id, ft.get("sessionId", ""),
                            "COMPLETED" if ft.get("responseCode", "00") == "00" else "ERROR",
                            ft.get("message", ""))


class RailRouter:
    """One tx_id, one rail. Routes by explicit request or default."""

    def __init__(self, rails: list[_Rail] | None = None, default: str = "mojaloop"):
        rails = rails or [MojaloopRail(), NIPRail()]
        self._rails = {r.name: r for r in rails}
        self._settled: dict[str, str] = {}  # tx_id -> rail
        self.default = default

    def transfer(self, req: PaymentRequest, rail: str = "") -> PaymentResult:
        rail = rail or self.default
        if rail not in self._rails:
            return PaymentResult(req.tx_id, rail, "", "ERROR", f"unknown rail {rail}")
        prior = self._settled.get(req.tx_id)
        if prior is not None and prior != rail:
            raise CrossRailReplay(
                f"{req.tx_id} already settled on {prior} — cannot replay on {rail}")
        rec = self._rails[rail].transfer(req)
        if rec.status in ("COMPLETED", "DUPLICATE"):
            self._settled[req.tx_id] = rail
        return rec

    def status(self, tx_id: str) -> str:
        rail = self._settled.get(tx_id)
        if rail is None:
            return "unknown"
        rec = self._rails[rail]._sent.get(tx_id)
        return rec.status if rec else "unknown"
