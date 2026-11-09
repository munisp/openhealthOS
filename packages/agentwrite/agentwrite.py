"""agentwrite.py — one pipeline for every agent read and write (ADR-0062).

The platform already owned every piece of the agent-write problem as a
separate package:

  - mcptools  (ADR-0060) — the governed transport: deny-by-default
    actors/tools, write-class calls pend for a human, hash-chained audit
  - writegate (ADR-0061) — the proposal lifecycle: capability gating,
    policy-only-denies before presentation, named-reviewer approvals,
    commit fidelity, AIAST + Provenance, independent read-back
  - fhirguard (ADR-0046) — the content verifier: recognised code systems,
    resolvable subject, spec-legal statuses, no invented facts
  - absentia  (ADR-0061) — reads that can prove what they searched:
    absence is evidence of search, not evidence of safety
  - fhircover (ADR-0061) — honest capability counting against a declared
    versioned denominator
  - agentgate (ADR-0048, shared/ Go) — the execution gate services call

Separate mechanisms are how an agent action slips BETWEEN them. This
module is the composition: one call surface where

  READ  = registry.call -> result wrapped in an absentia envelope
          (subject + as-of + search set; an empty search set refuses)
  WRITE = registry.call (pend, audited) -> builder -> writegate.propose
          with fhirguard as a PRE-PRESENTATION policy -> human decision
          -> writegate commit (exact resource, AIAST, Provenance,
          read-back)

Composition, not modification: mcptools, writegate, absentia and
fhircover are used through their public contracts; nothing here edits
their internals. Stdlib only (ADR-0005).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "mcptools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "writegate"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "absentia"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "fhirguard"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "fhircover"))

import absentia
import fhirguard
import mcptools
import writegate
from platform_coverage import platform_matrix

VERSION = "agentwrite@1.0.0"

# Static, configuration-owned denial text (ADR-0061): the policy-denied
# reason is never interpolated with patient data. The agent receives
# fhirguard's field-level problems separately — repairable errors belong
# to the caller, static reasons belong to the audit trail.
FHIRGUARD_DENIAL = ("ai-write validation failed (fhirguard) — resource "
                    "does not meet the platform's write-validation rules")


class AgentWriteError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class AgentWriteStack:
    """One governed surface over the five packages.

    now: injectable clock (callable -> str). Deterministic demos and
    tests inject; production wires the wall clock at the seam.
    """

    def __init__(self, *, actors=(), store=None, known_patients=(),
                 session_tag=None, now=None, extra_policies=()):
        self.store = store if store is not None else writegate.MemoryStore()
        self.registry = mcptools.ToolRegistry(actors=actors)
        self.known_patients = set(known_patients)
        self.now = now or (lambda: "1970-01-01T00:00:00Z")
        self.gate = writegate.WriteGate(
            self.store,
            policies=[self._fhirguard_policy, *extra_policies],
            session_tag=session_tag)
        self._builders = {}   # write tool name -> builder(args) -> dict
        self._readers = {}    # read tool name -> reader spec
        self._envelopes = []  # every issued envelope, in order
        self._chain = []
        self._prev = "genesis"

    # ---------------------------------------------------------- audit

    def _log(self, entry):
        entry = dict(entry, prev=self._prev)
        entry["hash"] = hashlib.sha256(_canon(entry)).hexdigest()
        self._prev = entry["hash"]
        self._chain.append(entry)

    def verify_audit(self):
        """All THREE chains must verify: transport, gate, and the
        stack's own cross-cutting chain."""
        if not (self.registry.verify_audit() and self.gate.verify_audit()):
            return False
        prev = "genesis"
        for entry in self._chain:
            body = {k: v for k, v in entry.items() if k != "hash"}
            if body["prev"] != prev:
                return False
            if hashlib.sha256(_canon(body)).hexdigest() != entry["hash"]:
                return False
            prev = entry["hash"]
        return True

    # --------------------------------------------- fhirguard as policy

    def _known_patient_ids(self):
        known = set(self.known_patients)
        for p in self.store.by_type("Patient"):
            # a stored patient is known under BOTH its store id and any
            # business id the resource carries
            for key in ("_id", "id"):
                if p.get(key):
                    known.add(p[key])
        return known

    def _fhirguard_policy(self, resource):
        """writegate policy seam: fhirguard runs BEFORE presentation.
        Denial reason is static configuration text (ADR-0061); the
        field-level problems are returned to the CALLING AGENT by the
        stack's call path — repairable errors belong to the caller,
        static reasons belong to the audit trail."""
        try:
            fhirguard.validate_ai_write(resource,
                                        self._known_patient_ids(),
                                        author="agent")
        except fhirguard.FHIRGuardError as exc:
            self._log({"event": "fhirguard-denied",
                       "problems": str(exc)})
            return FHIRGUARD_DENIAL
        return None

    # --------------------------------------------------- registration

    def register_read(self, name, *, description, store_name, query,
                      required_args=("subject",), not_checked=()):
        """A read tool whose result is an absentia envelope.

        query(args, store) -> iterable of findings.
        not_checked: [(store_name, reason)] — declared gaps travel with
        every result; silence about coverage is the defect absentia
        exists to prevent.
        """
        if not callable(query):
            raise AgentWriteError("a read tool needs a query function")

        def run(args):
            subject = args.get("subject")
            env = absentia.QueryEnvelope(subject, as_of=self.now())
            findings = list(query(args, self.store))
            env.record_search(store_name, filters=dict(args),
                              findings=findings)
            for nc_store, nc_reason in not_checked:
                env.declare_not_checked(nc_store, reason=nc_reason)
            result = {"findings": findings,
                      "search_set": [c for c in env._checked],
                      "not_checked": list(env._not_checked)}
            if not findings:
                result["absence"] = env.absence_claim(name)
            self._envelopes.append(env)
            self._log({"event": "envelope-issued", "tool": name,
                       "subject": subject, "store": store_name,
                       "n": len(findings)})
            return result

        self.registry.register(name, description=description,
                               risk="read", required_args=required_args,
                               fn=run)
        self._readers[name] = {"store": store_name}
        return name

    def register_write(self, name, *, description, required_args,
                       builder):
        """A write tool. The registry pends it (audited); the stack then
        runs builder(args) -> {"resource": ..., "rendering": ...} and
        routes the result through writegate. The builder NEVER commits."""
        if not callable(builder):
            raise AgentWriteError("a write tool needs a builder")

        def never_called(args):  # registry pends write risk pre-fn
            raise AgentWriteError("write builders run through the gate, "
                                  "never through the registry")

        self.registry.register(name, description=description,
                               risk="write",
                               required_args=required_args,
                               fn=never_called)
        self._builders[name] = builder
        return name

    # ------------------------------------------------------- tool face

    def list_tools(self, *, actor, host_can_render):
        """Capability gating at discovery: a host that cannot render is
        never SHOWN a write tool — it cannot even ask (ADR-0061)."""
        tools = self.registry.list_tools(actor=actor)
        if host_can_render:
            return tools
        return [t for t in tools if t["risk"] != "write"]

    def capabilities(self, *, host_can_render):
        return self.gate.capabilities(host_can_render=host_can_render)

    def call(self, name, *, actor, args, host_can_render=False):
        outcome = self.registry.call(name, actor=actor, args=args)
        if outcome.get("status") != "pending":
            return outcome  # ok / denied / error — registry already spoke
        # write-class call, allowlisted actor, args complete: the
        # registry pended it; now the stack builds the proposal.
        builder = self._builders.get(name)
        if builder is None:
            return {"status": "error",
                    "errors": [f"write tool {name} has no builder — "
                               "refusing to pend a write nothing can "
                               "construct"]}
        try:
            built = builder(args)
            resource = built["resource"]
            rendering = built["rendering"]
        except AgentWriteError:
            raise
        except Exception as exc:
            self._log({"event": "builder-failed", "tool": name,
                       "reason": str(exc)})
            return {"status": "error",
                    "errors": [f"builder failed: {exc}"]}
        self._log({"event": "bridge-invoked", "tool": name,
                   "actor": actor})
        # Pre-validate to obtain fhirguard's TAGGED COPY (the
        # ai-authored provenance tag travels with the committed
        # resource) and the field-level problems for the agent. The
        # gate policy remains the enforcement point — if validation
        # failed, the ORIGINAL is proposed and the policy denies,
        # recorded in the gate's own audit.
        try:
            validated = fhirguard.validate_ai_write(
                resource, self._known_patient_ids(), author=actor)
            problems = None
        except fhirguard.FHIRGuardError as exc:
            validated = resource
            problems = str(exc)
        try:
            proposal = self.gate.propose(validated, rendering,
                                         agent=actor,
                                         host_can_render=host_can_render)
        except writegate.WriteGateError as exc:
            self._log({"event": "gate-refused", "tool": name,
                       "reason": str(exc)})
            return {"status": "error", "errors": [str(exc)]}
        if proposal["status"] == "policy-denied":
            reply = dict(proposal)
            if problems:
                reply["problems"] = problems
            return reply
        return {"status": "pending",
                "proposal_id": proposal["proposal_id"],
                "rendering": proposal["rendering"],
                "note": "machines propose, people commit — this write "
                        "exists only as a proposal until a named "
                        "reviewer approves it"}

    # ------------------------------------------------- human decision

    def decide(self, proposal_id, decision, *, reviewer=None):
        self._log({"event": "decision-routed", "id": proposal_id,
                   "decision": decision,
                   "reviewer": reviewer or "(none)"})
        return self.gate.decide(proposal_id, decision, reviewer=reviewer)

    # ------------------------------------------------------ reporting

    def coverage(self):
        """The platform's honest coverage claim, denominator stated."""
        report = platform_matrix().report()
        self._log({"event": "coverage-read",
                   "fraction": report["fraction"],
                   "denominator": report["denominator"]})
        return report

    def envelope_count(self):
        return len(self._envelopes)
