"""NHIA / state-scheme claims adapter — submits adjudicated claims to the
national/scheme gateway contract (openapi/claims-gateway.yaml) and polls
status. Sandbox mode records submissions for tests and demos; live mode
posts to the scheme endpoint. Idempotent by claim_id: resubmission of a
known claim returns the original reference, never a duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from httpclient import HTTPClient


@dataclass
class Submission:
    claim_id: str
    reference: str
    status: str  # received | duplicate | error
    detail: str = ""


class NHIAAdapter:
    def __init__(self, base_url: str = "", token: str = "", sandbox: bool = True):
        self.sandbox = sandbox
        self.client = None if sandbox else HTTPClient(base_url, token)
        self._submitted: dict[str, Submission] = {}
        self._seq = 0

    def submit_claim(self, claim: dict, decision: dict) -> Submission:
        """Submit an APPROVEd claim with its adjudication decision."""
        cid = claim.get("claim_id", "")
        if not cid:
            return Submission("", "", "error", "claim_id required")
        if decision.get("decision") != "APPROVE":
            return Submission(cid, "", "error",
                              f"only APPROVE decisions are submitted, got {decision.get('decision')}")
        if cid in self._submitted:
            prior = self._submitted[cid]
            return Submission(cid, prior.reference, "duplicate")
        if self.sandbox:
            self._seq += 1
            sub = Submission(cid, f"NHIA-SBX-{self._seq:06d}", "received")
            self._submitted[cid] = sub
            return sub
        resp = self.client.request("POST", "/claims", {"claim": claim, "decision": decision})
        sub = Submission(cid, resp.get("reference", ""), resp.get("status", "received"))
        self._submitted[cid] = sub
        return sub

    def status(self, claim_id: str) -> str:
        if self.sandbox:
            sub = self._submitted.get(claim_id)
            return sub.status if sub else "unknown"
        resp = self.client.request("GET", f"/claims/{claim_id}")
        return resp.get("status", "unknown")

    def submitted(self) -> list[Submission]:
        return list(self._submitted.values())
