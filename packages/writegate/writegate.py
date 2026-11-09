"""writegate.py — approval-gated agent writes (ADR-0061).

Clean-room implementation of the Approval-Gated Agent Writes protocol
(cbetz/last-ehr, Apache-2.0 — protocol adopted, code not embedded).
The protocol specifies the layer nobody owned: CDS Hooks covers
EHR-initiated suggestions, the AI Transparency IG covers after-the-fact
provenance, and neither covers an AGENT-initiated write that a human
must approve before it exists. agentgate (ADR-0048) gates execution;
fhirguard (ADR-0046) validates the resource; this module owns the
proposal lifecycle between them.

The laws (protocol MUSTs, restated in house dialect):

  - A write tool NEVER executes. It produces a Proposal: the exact
    resource plus the exact human-readable rendering the reviewer will
    see. What the reviewer sees is what will save.
  - DECISIONS ARE EXPLICIT: approved / denied / unavailable are three
    different answers, and only the first commits. There is no default,
    no approving timeout, no batch approval.
  - EVERYTHING FAILS CLOSED: transport failure, ambiguity, a host that
    cannot render — nothing is written and the agent-visible result
    says so.
  - CAPABILITY GATING: a host that cannot render proposals is never
    offered write capability at all. A degraded host cannot even ask.
  - POLICY CAN ONLY DENY. A policy outcome is never an approval, is
    evaluated BEFORE presentation (reviewers never decide writes that
    cannot commit), and a policy denial is distinguishable from a
    reviewer's denial — it attributes the outcome to configuration,
    never to a human. Policy evaluation failure denies. Policy denial
    reasons are STATIC configuration text — never interpolated with
    patient data (the error path is not an exfiltration channel).
  - COMMIT FIDELITY: what commits is exactly the proposed resource;
    only mechanical metadata (server id/version, commit-time stamp,
    audit markers) may be added.
  - ATTRIBUTION: committed writes carry the AIAST security label
    (AI-asserted) and a Provenance resource naming the agent as author
    and the reviewer as verifier.
  - READ-BACK: after commit, the store is re-read independently and
    compared against the proposal — the tool's own word is not
    evidence.
"""

from __future__ import annotations

import hashlib
import json

VERSION = "writegate@1.0.0"

AIAST = {"system": "http://terminology.hl7.org/CodeSystem/"
                   "v3-ObservationValue",
         "code": "AIAST",
         "display": "Artificial Intelligence asserted"}

DECISIONS = ("approved", "denied", "unavailable", "policy-denied")


class WriteGateError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class MemoryStore:
    """Minimal FHIR-ish store for the gate's seams. A real deployment
    injects a Medplum/shr-service adapter with the same surface."""

    def __init__(self):
        self._resources = {}
        self._counter = 0

    def create(self, resource):
        self._counter += 1
        rid = f"r{self._counter}"
        stored = dict(resource)
        stored.setdefault("meta", {})["versionId"] = "1"
        self._resources[rid] = stored
        return rid, stored

    def read(self, rid):
        return self._resources.get(rid)

    def by_type(self, rtype):
        return [dict(r, _id=k) for k, r in self._resources.items()
                if r.get("resourceType") == rtype]


class Proposal:
    def __init__(self, pid, resource, rendering, agent, session=None):
        self.id = pid
        self.resource = resource
        self.rendering = rendering
        self.agent = agent
        self.session = session
        self.fingerprint = hashlib.sha256(_canon(resource)).hexdigest()


class WriteGate:
    """Proposal -> policy -> decision -> commit -> read-back -> audit."""

    def __init__(self, store, *, policies=(), session_tag=None):
        self.store = store
        self.policies = list(policies)
        self.session_tag = session_tag  # isolation profile (ADR-0061 §5)
        self._proposals = {}
        self._audit = []
        self._prev = "genesis"
        self._n = 0

    # ---------------------------------------------------------- audit

    def _log(self, entry):
        entry = dict(entry, prev=self._prev)
        entry["hash"] = hashlib.sha256(_canon(entry)).hexdigest()
        self._prev = entry["hash"]
        self._audit.append(entry)

    def verify_audit(self):
        prev = "genesis"
        for entry in self._audit:
            body = {k: v for k, v in entry.items() if k != "hash"}
            if body["prev"] != prev:
                return False
            if hashlib.sha256(_canon(body)).hexdigest() != entry["hash"]:
                return False
            prev = entry["hash"]
        return True

    # ------------------------------------------------------- propose

    def capabilities(self, *, host_can_render):
        """Capability gating: write capability is only OFFERED to hosts
        that can render proposals. A degraded host cannot even ask."""
        return {"write": bool(host_can_render)}

    def propose(self, resource, rendering, *, agent, host_can_render,
                session=None):
        if not host_can_render:
            self._log({"event": "capability-refused", "agent": agent})
            raise WriteGateError(
                "host cannot render proposals — write capability was "
                "never offered; nothing can be written")
        if not (rendering or "").strip():
            raise WriteGateError("a proposal without a rendering asks "
                                 "the reviewer to sign a blank page")
        if not isinstance(resource, dict) or "resourceType" not in resource:
            raise WriteGateError("proposal needs a FHIR resource")
        # policy BEFORE presentation: reviewers never decide writes
        # that cannot commit. Policy failure denies.
        for policy in self.policies:
            try:
                verdict = policy(resource)
            except Exception:
                verdict = "policy evaluation failed"
            if verdict is not None:
                if not isinstance(verdict, str):
                    raise WriteGateError("policy denial reasons must be "
                                         "static text")
                self._log({"event": "policy-denied", "agent": agent,
                           "reason": verdict})
                return {"status": "policy-denied", "reason": verdict,
                        "committed": False}
        self._n += 1
        pid = f"prop-{self._n}"
        prop = Proposal(pid, resource, rendering, agent,
                        session or self.session_tag)
        self._proposals[pid] = prop
        self._log({"event": "proposed", "id": pid, "agent": agent,
                   "fingerprint": prop.fingerprint})
        return {"status": "proposed", "proposal_id": pid,
                "rendering": rendering, "committed": False}

    # ------------------------------------------------------- decide

    def decide(self, proposal_id, decision, *, reviewer=None):
        prop = self._proposals.get(proposal_id)
        if prop is None:
            raise WriteGateError(f"unknown proposal: {proposal_id}")
        if decision not in ("approved", "denied", "unavailable"):
            raise WriteGateError(f"not a reviewer decision: {decision}")
        if decision == "denied":
            self._log({"event": "denied", "id": proposal_id,
                       "reviewer": reviewer})
            del self._proposals[proposal_id]
            return {"status": "denied", "committed": False,
                    "attributed_to": "reviewer"}
        if decision == "unavailable":
            self._log({"event": "unavailable", "id": proposal_id})
            del self._proposals[proposal_id]
            return {"status": "unavailable", "committed": False,
                    "attributed_to": "host"}
        if not reviewer:
            raise WriteGateError("an approval without a named reviewer "
                                 "is not an approval")
        return self._commit(prop, reviewer)

    # -------------------------------------------------------- commit

    def _commit(self, prop, reviewer):
        # commit fidelity: exactly the proposed resource plus
        # mechanical metadata and the audit markers
        resource = json.loads(_canon(prop.resource))  # deep copy
        meta = resource.setdefault("meta", {})
        meta.setdefault("security", [])
        if AIAST not in meta["security"]:
            meta["security"].append(AIAST)
        if prop.session:
            meta.setdefault("tag", [])
            meta["tag"].append({"system": "https://openhealthos.org/"
                                          "session",
                                "code": prop.session})
        rid, stored = self.store.create(resource)
        provenance = {
            "resourceType": "Provenance",
            "target": [{"reference": f"{resource['resourceType']}/{rid}"}],
            "agent": [
                {"type": {"coding": [{"code": "author"}]},
                 "who": {"display": f"agent:{prop.agent}"}},
                {"type": {"coding": [{"code": "verifier"}]},
                 "who": {"display": f"reviewer:{reviewer}"}},
            ],
        }
        prov_id, _ = self.store.create(provenance)
        # read-back: the store is re-read independently; the tool's own
        # word is not evidence
        readback = self.store.read(rid)
        if readback is None:
            self._log({"event": "commit-unverified", "id": prop.id})
            raise WriteGateError("commit could not be verified by "
                                 "independent read — reporting failure")
        # clinical content must match the proposal byte-for-byte
        seen = {k: v for k, v in readback.items() if k != "meta"}
        proposed = {k: v for k, v in prop.resource.items() if k != "meta"}
        if _canon(seen) != _canon(proposed):
            self._log({"event": "commit-mismatch", "id": prop.id})
            raise WriteGateError("read-back differs from the approved "
                                 "proposal — refusing to confirm")
        self._log({"event": "committed", "id": prop.id,
                   "resource_id": rid, "reviewer": reviewer,
                   "provenance_id": prov_id})
        del self._proposals[prop.id]
        return {"status": "approved", "committed": True,
                "resource_id": rid, "provenance_id": prov_id,
                "verified_by": "read-back"}
