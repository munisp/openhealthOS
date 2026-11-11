"""demo_agentwrite.py — deterministic seed for the agent-write board.

One clinic morning with a triage agent, reproducible byte-for-byte:
the agent searches allergies (absence stated with its search set),
proposes a malaria Condition (reviewer approves — commit carries
AIAST + ai-authored + Provenance + read-back), proposes an Observation
with an invented code system (fhirguard denies BEFORE any reviewer is
asked), and proposes a sepsis note the reviewer denies. A degraded
host is never shown the write tools. The coverage card counts 27/27
against the declared denominator.

Same doctrine as vhpilot/vhdashx: a demo you cannot reproduce is a
story you cannot audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

from agentwrite import AgentWriteStack

DEMO_VERSION = "agentwrite-demo@1.0.0"

CLOCK = lambda: "2026-11-10T08:00:00Z"  # noqa: E731

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "apps",
                   "pwa", "agentwrite.json")


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return dict(payload, sha256=digest)


def seed_stack():
    stack = AgentWriteStack(actors=("triage-llm",), session_tag="opd-a",
                            now=CLOCK)
    stack.store.create({"resourceType": "Patient", "id": "pax-sim-0001",
                        "name": [{"family": "Sim",
                                  "given": ["Adaeze"]}],
                        "gender": "female", "birthDate": "1991-04-02"})

    stack.register_read(
        "patient.allergy_search",
        description="Search the allergy store for a patient",
        store_name="shr-allergyintolerance",
        query=lambda args, store: [
            r for r in store.by_type("AllergyIntolerance")
            if r.get("patient", {}).get("reference",
               "") == f"Patient/{args['subject']}"],
        not_checked=[("paper-chart",
                      "no adapter configured for legacy paper charts")])

    def build_condition(args):
        return {"resource": {
                    "resourceType": "Condition",
                    "subject": {"reference": f"Patient/{args['subject']}"},
                    "code": {"coding": [{
                        "system": "http://hl7.org/fhir/sid/icd-10",
                        "code": "B54", "display": "Malaria"}]},
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "recordedDate": "2026-11-10"},
                "rendering": ("Record condition — Malaria (ICD-10 B54), "
                              "clinical status: active, recorded "
                              f"2026-11-10, patient {args['subject']}.")}
    stack.register_write("condition.record",
                         description="Propose a Condition for approval",
                         required_args=("subject",),
                         builder=build_condition)

    def build_observation(args):
        return {"resource": {
                    "resourceType": "Observation",
                    "subject": {"reference": f"Patient/{args['subject']}"},
                    "status": "final",
                    "code": {"coding": [{
                        "system": "http://example.org/made-up",
                        "code": "SEPSIS-RISK"}]},
                    "effectiveDateTime": "2026-11-10T08:00:00Z"},
                "rendering": "Record observation: sepsis risk score"}
    stack.register_write("observation.record",
                         description="Propose an Observation for approval",
                         required_args=("subject",),
                         builder=build_observation)

    def build_note(args):
        return {"resource": {
                    "resourceType": "Condition",
                    "subject": {"reference": f"Patient/{args['subject']}"},
                    "code": {"coding": [{
                        "system": "http://hl7.org/fhir/sid/icd-10",
                        "code": "A41.9",
                        "display": "Sepsis, unspecified organism"}]},
                    "clinicalStatus": {"coding": [{"code": "active"}]},
                    "recordedDate": "2026-11-10"},
                "rendering": ("Record condition — Sepsis, unspecified "
                              "(ICD-10 A41.9), active, patient "
                              f"{args['subject']}.")}
    stack.register_write("condition.record.sepsis",
                         description="Propose a sepsis Condition",
                         required_args=("subject",),
                         builder=build_note)
    return stack


def run_demo():
    stack = seed_stack()
    events = []

    # 1. capability gating: a degraded host is never shown write tools
    degraded = stack.list_tools(actor="triage-llm",
                                host_can_render=False)
    full = stack.list_tools(actor="triage-llm", host_can_render=True)
    events.append({"kind": "capability",
                   "degraded_host_tools": [t["name"] for t in degraded],
                   "rendering_host_tools": [t["name"] for t in full],
                   "note": "a host that cannot render proposals is never "
                           "offered write capability — it cannot even "
                           "ask"})

    # 2. allergy read: absence with its search set, paper chart NOT CHECKED
    allergy = stack.call("patient.allergy_search", actor="triage-llm",
                         args={"subject": "pax-sim-0001"})
    events.append({"kind": "read", "tool": "patient.allergy_search",
                   "outcome": allergy})

    # 3. valid condition write: pend -> approve -> commit + read-back
    pend = stack.call("condition.record", actor="triage-llm",
                      args={"subject": "pax-sim-0001"},
                      host_can_render=True)
    approved = stack.decide(pend["proposal_id"], "approved",
                            reviewer="dr-sim-ada")
    committed = stack.store.read(approved["resource_id"])
    events.append({"kind": "write-approved", "tool": "condition.record",
                   "proposal": pend, "decision": approved,
                   "committed_meta": committed["meta"]})

    # 4. invented code system: fhirguard denies before any reviewer
    denied = stack.call("observation.record", actor="triage-llm",
                        args={"subject": "pax-sim-0001"},
                        host_can_render=True)
    events.append({"kind": "write-policy-denied",
                   "tool": "observation.record", "outcome": denied,
                   "note": "policy runs BEFORE presentation — no "
                           "reviewer was ever asked"})

    # 5. reviewer denial: attributed to the human, nothing commits
    pend2 = stack.call("condition.record.sepsis", actor="triage-llm",
                       args={"subject": "pax-sim-0001"},
                       host_can_render=True)
    refused = stack.decide(pend2["proposal_id"], "denied",
                           reviewer="dr-sim-ada")
    events.append({"kind": "write-reviewer-denied",
                   "tool": "condition.record.sepsis",
                   "proposal": pend2, "decision": refused})

    coverage = stack.coverage()
    chains_ok = stack.verify_audit()

    payload = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK(),
        "actor": "triage-llm",
        "reviewer": "dr-sim-ada",
        "patient": "pax-sim-0001",
        "events": events,
        "coverage": {"fraction": coverage["fraction"],
                     "denominator": coverage["denominator"],
                     "note": coverage["note"]},
        "audit": {"registry_chain": "verified" if chains_ok else "BROKEN",
                  "gate_chain": "verified" if chains_ok else "BROKEN",
                  "stack_chain": "verified" if chains_ok else "BROKEN",
                  "envelopes_issued": stack.envelope_count()},
    }
    return _stamp(payload)


def main():
    payload = run_demo()
    out = os.path.normpath(OUT)
    payloadio.write_text(out, json.dumps(payload, indent=1) + "\n")
    print(f"wrote {out} sha256={payload['sha256'][:12]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
