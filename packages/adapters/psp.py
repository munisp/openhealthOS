"""Nigerian PSP adapter — collections and payouts (module 5).

Premium micro-collections (₦500–2,000 instalments) and automated provider
payouts ride Nigerian PSPs (Paystack/Flutterwave patterns, Mojaloop-informed).
Every operation carries an idempotency key — money operations must be
exactly-once even when the network lies about failure. Sandbox mode is a
deterministic ledger for tests; live mode posts to the PSP API.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from httpclient import HTTPClient

MIN_COLLECTION_NGN = 500
MAX_COLLECTION_NGN = 2000  # micro-instalment band from the product spec


@dataclass
class MoneyResult:
    ref: str
    status: str        # success | duplicate | rejected | error
    amount_ngn: int
    detail: str = ""


class PSPAdapter:
    def __init__(self, base_url: str = "", token: str = "",
                 provider: str = "sandbox", sandbox: bool = True):
        self.provider = provider
        self.sandbox = sandbox
        self.client = None if sandbox else HTTPClient(base_url, token)
        self._ops: dict[str, MoneyResult] = {}   # idempotency key -> result

    @staticmethod
    def idempotency_key(kind: str, wallet: str, amount: int, nonce: str) -> str:
        raw = f"{kind}|{wallet}|{amount}|{nonce}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def collect(self, wallet_id: str, amount_ngn: int, nonce: str) -> MoneyResult:
        """Collect a premium micro-instalment from a member wallet."""
        if not MIN_COLLECTION_NGN <= amount_ngn <= MAX_COLLECTION_NGN:
            return MoneyResult("", "rejected", amount_ngn,
                               f"instalments are ₦{MIN_COLLECTION_NGN}–₦{MAX_COLLECTION_NGN}")
        return self._do("collect", wallet_id, amount_ngn, nonce)

    def payout(self, facility_account: str, amount_ngn: int, nonce: str) -> MoneyResult:
        """Automated provider payout — any positive amount."""
        if amount_ngn <= 0:
            return MoneyResult("", "rejected", amount_ngn, "payout must be positive")
        return self._do("payout", facility_account, amount_ngn, nonce)

    def _do(self, kind: str, target: str, amount: int, nonce: str) -> MoneyResult:
        key = self.idempotency_key(kind, target, amount, nonce)
        if key in self._ops:
            prior = self._ops[key]
            return MoneyResult(prior.ref, "duplicate", amount)
        if self.sandbox:
            res = MoneyResult(f"{self.provider.upper()}-{key[:10]}", "success", amount)
        else:
            resp = self.client.request("POST", f"/{kind}",
                                       {"target": target, "amount_ngn": amount,
                                        "idempotency_key": key})
            res = MoneyResult(resp.get("ref", key[:10]), resp.get("status", "success"), amount)
        self._ops[key] = res
        return res

    def operations(self) -> list[MoneyResult]:
        return list(self._ops.values())
