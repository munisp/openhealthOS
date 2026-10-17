#!/usr/bin/env python3
"""Payer network — one claim, many payers (ADR-0012).

The adjudicator decides WHETHER a claim pays; this module decides WHERE it
goes. Nigeria's coverage reality is a federation of payers: NHIA schemes
(BHCPF for the vulnerable, state basic packages, formal-sector NHIA) plus
commercial HMOs with their own submission endpoints. A claim must route to
exactly one payer — determined by the member's scheme — and must never be
paid twice, not even by two different payers.

Rules enforced here:
  * Unknown scheme → refused. An unregistered scheme is a fraud smell; the
    claim never leaves the building.
  * (payer, claim_id) is idempotent: a retry returns the same reference.
  * A claim_id already routed to payer A cannot be re-routed to payer B —
    double-dipping across payers is the classic HMO fraud pattern.

Adapters are a seam: sandbox mode is deterministic and offline; live mode
POSTs {claim, decision} to the payer's endpoint with a bearer token. Same
interface either way, so edge facilities queue submissions until the uplink
exists. No proprietary SDKs (ADR-0005).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass


class UnknownScheme(KeyError):
    """The member's scheme is not registered to any payer."""


class CrossPayerReplay(ValueError):
    """claim_id already routed to a different payer — double-dip refused."""


@dataclass(frozen=True)
class Payer:
    payer_id: str
    name: str
    kind: str               # government | commercial
    schemes: tuple[str, ...]


# Default registry. Production loads this from config (one payer entry per
# signed contract); the shape is deliberately plain data.
DEFAULT_PAYERS: tuple[Payer, ...] = (
    Payer("NHIA", "National Health Insurance Authority", "government",
          ("BHCPF-VUL", "STATE-BASIC", "NHIA-FORMAL")),
    Payer("HYGEIA", "Hygeia HMO", "commercial", ("HYGEIA-COMM",)),
    Payer("RELIANCE", "Reliance HMO", "commercial", ("RELIANCE-CARE",)),
)


@dataclass
class SubmissionRecord:
    claim_id: str
    payer_id: str
    reference: str
    status: str    # received | duplicate | error
    detail: str = ""


class PayerAdapter:
    """One payer endpoint. sandbox=True: deterministic in-memory references.
    Live: POST {claim, decision} → {reference, status} with bearer token."""

    def __init__(self, payer: Payer, endpoint: str = "", token: str = "", sandbox: bool = True):
        self.payer = payer
        self.sandbox = sandbox
        self.endpoint = endpoint
        self.token = token
        self._sent: dict[str, SubmissionRecord] = {}
        self._seq = 0

    def submit(self, claim: dict, decision: dict) -> SubmissionRecord:
        cid = claim.get("claim_id", "")
        if not cid:
            return SubmissionRecord(cid, self.payer.payer_id, "", "error", "claim_id required")
        if decision.get("decision") != "APPROVE":
            return SubmissionRecord(cid, self.payer.payer_id, "", "error",
                                    f"only APPROVE decisions are submitted, got {decision.get('decision')}")
        if cid in self._sent:
            prior = self._sent[cid]
            return SubmissionRecord(cid, self.payer.payer_id, prior.reference, "duplicate")
        if self.sandbox:
            self._seq += 1
            rec = SubmissionRecord(cid, self.payer.payer_id,
                                   f"{self.payer.payer_id}-SBX-{self._seq:06d}", "received")
            self._sent[cid] = rec
            return rec
        body = json.dumps({"claim": claim, "decision": decision}).encode()
        req = urllib.request.Request(self.endpoint, data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            out = json.loads(resp.read())
        rec = SubmissionRecord(cid, self.payer.payer_id,
                               out.get("reference", ""), out.get("status", "received"))
        self._sent[cid] = rec
        return rec

    def status(self, claim_id: str) -> str:
        rec = self._sent.get(claim_id)
        return rec.status if rec else "unknown"

    def submissions(self) -> list[SubmissionRecord]:
        return list(self._sent.values())


class PayerRouter:
    """Routes adjudicated claims to the payer that owns the member's scheme."""

    def __init__(self, payers: tuple[Payer, ...] = DEFAULT_PAYERS, sandbox: bool = True):
        self._by_scheme: dict[str, Payer] = {}
        self._adapters: dict[str, PayerAdapter] = {}
        self._routed: dict[str, str] = {}  # claim_id -> payer_id (cross-payer guard)
        for payer in payers:
            for scheme in payer.schemes:
                if scheme in self._by_scheme:
                    raise ValueError(f"scheme {scheme} registered to two payers")
                self._by_scheme[scheme] = payer
            self._adapters[payer.payer_id] = PayerAdapter(payer, sandbox=sandbox)

    def payer_for(self, scheme: str) -> Payer:
        try:
            return self._by_scheme[scheme]
        except KeyError:
            raise UnknownScheme(scheme) from None

    def submit(self, claim: dict, decision: dict) -> SubmissionRecord:
        scheme = decision.get("scheme", "")
        payer = self.payer_for(scheme)  # unknown scheme refused here
        cid = claim.get("claim_id", "")
        prior = self._routed.get(cid)
        if prior is not None and prior != payer.payer_id:
            raise CrossPayerReplay(
                f"claim {cid} already routed to {prior} — cannot also submit to {payer.payer_id}")
        self._routed[cid] = payer.payer_id
        return self._adapters[payer.payer_id].submit(claim, decision)

    def status(self, claim_id: str) -> str:
        payer_id = self._routed.get(claim_id)
        if payer_id is None:
            return "unknown"
        return self._adapters[payer_id].status(claim_id)

    def submissions(self) -> list[SubmissionRecord]:
        out: list[SubmissionRecord] = []
        for adapter in self._adapters.values():
            out.extend(adapter.submissions())
        return out

    def by_payer(self) -> dict[str, int]:
        """Ops view: how many claims went to each payer."""
        return {pid: len(a.submissions()) for pid, a in self._adapters.items()}

    def register_payer(self, payer: Payer, endpoint: str = "", token: str = "") -> None:
        """Add a payer at runtime (a newly signed HMO contract)."""
        for scheme in payer.schemes:
            if scheme in self._by_scheme:
                raise ValueError(f"scheme {scheme} already registered")
        for scheme in payer.schemes:
            self._by_scheme[scheme] = payer
        self._adapters[payer.payer_id] = PayerAdapter(
            payer, endpoint=endpoint, token=token, sandbox=not endpoint)
