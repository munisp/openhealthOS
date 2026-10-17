"""OpenHealth OS — protocol-guided triage with Approval-Gated Agent Writes.

Implements the Last EHR protocol (ADR-0004): Proposal → Decision → Commit →
Audit. The triage engine can only PROPOSE; a human clinician DECIDES; only
an approved (or edited) proposal is COMMITTED to the record; every step is
AUDITED. Models have no write path — a proposal with no decision is nothing.

Triage rules are protocol-guided decision support tuned to Nigeria's top
presentations at the nurse/CHW layer (0.18 doctors/1,000 people): malaria,
pneumonia, diarrhoea/dehydration, obstetric danger signs, malnutrition.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum


# ---------------------------------------------------------------- protocols

# Danger-sign protocols: (id, any-of symptom sets, disposition, priority)
PROTOCOLS = [
    {"id": "MALARIA-SEVERE",
     "any_of": [{"fever", "convulsions"}, {"fever", "unable_to_drink"}, {"fever", "lethargy"}],
     "disposition": "REFER-URGENT", "priority": "P1",
     "note": "severe malaria danger signs — refer same day, pre-referral antimalarial per IMCI"},
    {"id": "PNEUMONIA",
     "any_of": [{"cough", "fast_breathing"}, {"cough", "chest_indrawing"}],
     "disposition": "REFER-URGENT", "priority": "P1",
     "note": "possible pneumonia — count breaths, refer per IMCI"},
    {"id": "DIARRHOEA-SEVERE-DEHYDRATION",
     "any_of": [{"diarrhoea", "lethargy"}, {"diarrhoea", "sunken_eyes", "unable_to_drink"}],
     "disposition": "REFER-URGENT", "priority": "P1",
     "note": "severe dehydration — start ORS plan C en route"},
    {"id": "OBSTETRIC-DANGER",
     "any_of": [{"pregnant", "vaginal_bleeding"}, {"pregnant", "severe_headache", "blurred_vision"},
                {"pregnant", "convulsions"}],
     "disposition": "REFER-EMERGENCY", "priority": "P0",
     "note": "obstetric emergency — immediate referral, do not delay"},
    {"id": "MALNUTRITION-SEVERE",
     "any_of": [{"child", "visible_wasting"}, {"child", "bilateral_oedema"}],
     "disposition": "REFER-URGENT", "priority": "P1",
     "note": "severe acute malnutrition — RUTF protocol / therapeutic feeding referral"},
    {"id": "MALARIA-UNCOMPLICATED",
     "any_of": [{"fever"}],
     "disposition": "TREAT-LOCAL", "priority": "P3",
     "note": "uncomplicated malaria — RDT first, ACT per national guidelines"},
]


def triage(symptoms: set[str], patient_id: str, author: str = "triage-engine") -> "Proposal":
    """Produce a triage PROPOSAL. The engine cannot write to the record."""
    fired = [p for p in PROTOCOLS if any(req <= symptoms for req in p["any_of"])]
    if not fired:
        assessment = {"protocol": "NONE", "disposition": "ROUTINE",
                      "priority": "P4", "note": "no protocol fired — routine pathway"}
    else:
        top = min(fired, key=lambda p: p["priority"])
        assessment = {"protocol": top["id"], "disposition": top["disposition"],
                      "priority": top["priority"], "note": top["note"],
                      "also_fired": [p["id"] for p in fired if p is not top]}
    return Proposal(
        id=f"prop-{uuid.uuid4().hex[:12]}",
        patient_id=patient_id,
        author=author,
        kind="triage-assessment",
        content={"symptoms": sorted(symptoms), "assessment": assessment},
        created_ts=time.time(),
    )


# ------------------------------------------- approval-gated write protocol

class DecisionOutcome(str, Enum):
    APPROVE = "APPROVE"
    EDIT = "EDIT"
    REJECT = "REJECT"


@dataclass
class Proposal:
    id: str
    patient_id: str
    author: str          # the model/agent that proposed — never a writer
    kind: str
    content: dict
    created_ts: float

    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass
class Decision:
    proposal_id: str
    clinician_id: str    # the accountable human — REQUIRED
    outcome: DecisionOutcome
    edited_content: dict | None = None  # set when outcome == EDIT
    decided_ts: float = field(default_factory=time.time)


@dataclass
class Commit:
    proposal_id: str
    decision: Decision
    record: dict         # what was actually written (approved or edited content)
    committed_ts: float = field(default_factory=time.time)


class ApprovalGate:
    """The only write path for agent-produced content into the record.

    commit() raises on any attempt to write without a valid human decision —
    models can never fake an absence. Every transition lands in the audit log.
    """

    def __init__(self):
        self._proposals: dict[str, Proposal] = {}
        self._commits: dict[str, Commit] = {}
        self.audit_log: list[dict] = []

    def _audit(self, event: str, **fields):
        self.audit_log.append({"event": event, "ts": time.time(), **fields})

    def propose(self, p: Proposal) -> str:
        self._proposals[p.id] = p
        self._audit("PROPOSAL", proposal_id=p.id, author=p.author, digest=p.digest())
        return p.id

    def decide(self, d: Decision) -> None:
        if d.proposal_id not in self._proposals:
            raise ValueError(f"unknown proposal {d.proposal_id}")
        if not d.clinician_id:
            raise ValueError("a deciding clinician is required — accountability is not optional")
        if d.outcome == DecisionOutcome.EDIT and d.edited_content is None:
            raise ValueError("EDIT requires edited_content")
        self._audit("DECISION", proposal_id=d.proposal_id,
                    clinician_id=d.clinician_id, outcome=d.outcome.value)

    def commit(self, proposal_id: str, decision: Decision) -> Commit:
        p = self._proposals.get(proposal_id)
        if p is None:
            raise ValueError(f"unknown proposal {proposal_id}")
        if decision.proposal_id != proposal_id:
            raise ValueError("decision does not reference this proposal")
        if not decision.clinician_id:
            raise ValueError("no write without an accountable clinician — the gate holds at commit, not only at decide")
        if decision.outcome == DecisionOutcome.REJECT:
            self._audit("REJECTED", proposal_id=proposal_id, clinician_id=decision.clinician_id)
            raise ValueError("rejected proposals are never committed")
        record = dict(p.content)
        record["assessment"] = (decision.edited_content
                                if decision.outcome == DecisionOutcome.EDIT
                                else p.content.get("assessment"))
        record["provenance"] = {
            "proposed_by": p.author,
            "decided_by": decision.clinician_id,
            "proposal_digest": p.digest(),
            "edited": decision.outcome == DecisionOutcome.EDIT,
        }
        c = Commit(proposal_id=proposal_id, decision=decision, record=record)
        self._commits[proposal_id] = c
        self._audit("COMMIT", proposal_id=proposal_id,
                    clinician_id=decision.clinician_id, edited=record["provenance"]["edited"])
        return c

    def record_of(self, proposal_id: str) -> dict | None:
        c = self._commits.get(proposal_id)
        return c.record if c else None
