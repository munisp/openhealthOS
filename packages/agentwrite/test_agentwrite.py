"""test_agentwrite.py — the integration pipeline, pinned.

Every test asserts the SEAM, not just the component: registry pends,
gate proposes, fhirguard tags, absentia envelopes, coverage reports,
three audit chains verify.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import agentwrite
from agentwrite import AgentWriteError, AgentWriteStack

CLOCK = lambda: "2026-11-10T08:00:00Z"  # noqa: E731

PATIENT = {"resourceType": "Patient", "id": "pax-sim-0001",
           "name": [{"family": "Sim", "given": ["Adaeze"]}]}

MALARIA_CONDITION = {
    "resourceType": "Condition",
    "subject": {"reference": "Patient/pax-sim-0001"},
    "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10",
                         "code": "B54", "display": "Malaria"}]},
    "clinicalStatus": {"coding": [{"code": "active"}]},
    "recordedDate": "2026-11-10",
}

INVENTED_CODE_OBS = {
    "resourceType": "Observation",
    "subject": {"reference": "Patient/pax-sim-0001"},
    "status": "final",
    "code": {"coding": [{"system": "http://example.org/made-up",
                         "code": "XYZ"}]},
    "effectiveDateTime": "2026-11-10T08:00:00Z",
}


def _seeded_stack(**kw):
    stack = AgentWriteStack(actors=("triage-llm",), now=CLOCK, **kw)
    stack.store.create(PATIENT)
    return stack


def _register_allergy_read(stack):
    return stack.register_read(
        "patient.allergy_search",
        description="Search the allergy store for a patient",
        store_name="shr-allergyintolerance",
        query=lambda args, store: [
            r for r in store.by_type("AllergyIntolerance")
            if r.get("patient", {}).get("reference",
               "") == f"Patient/{args['subject']}"],
        not_checked=[("paper-chart",
                      "no adapter configured for legacy paper charts")])


def _register_condition_write(stack):
    def build(args):
        return {"resource": dict(MALARIA_CONDITION,
                                 subject={"reference":
                                          f"Patient/{args['subject']}"}),
                "rendering": ("Record condition: Malaria (B54), active, "
                              f"for patient {args['subject']}")}
    return stack.register_write(
        "condition.record",
        description="Propose a Condition record for human approval",
        required_args=("subject",), builder=build)


# ------------------------------------------------------------- reads


def test_read_returns_envelope_with_search_set_and_not_checked():
    stack = _seeded_stack()
    _register_allergy_read(stack)
    out = stack.call("patient.allergy_search", actor="triage-llm",
                     args={"subject": "pax-sim-0001"})
    assert out["status"] == "ok"
    result = out["result"]
    assert result["findings"] == []
    assert result["search_set"][0]["store"] == "shr-allergyintolerance"
    assert result["not_checked"][0]["store"] == "paper-chart"
    # absence claim names searched vs not-checked — never one word
    assert "NOT CHECKED" in result["absence"]["verdict"]
    assert stack.envelope_count() == 1


def test_read_with_findings_has_no_absence_claim():
    stack = _seeded_stack()
    stack.store.create({"resourceType": "AllergyIntolerance",
                        "patient": {"reference": "Patient/pax-sim-0001"},
                        "code": {"text": "artemisinins"}})
    _register_allergy_read(stack)
    out = stack.call("patient.allergy_search", actor="triage-llm",
                     args={"subject": "pax-sim-0001"})
    assert out["status"] == "ok"
    assert len(out["result"]["findings"]) == 1
    assert "absence" not in out["result"]


def test_read_denied_for_unlisted_actor():
    stack = _seeded_stack()
    _register_allergy_read(stack)
    out = stack.call("patient.allergy_search", actor="rogue-bot",
                     args={"subject": "pax-sim-0001"})
    assert out["status"] == "denied"
    assert stack.envelope_count() == 0  # no envelope on a denied read


# ------------------------------------------------------------ writes


def test_write_pends_then_commits_with_full_attribution():
    stack = _seeded_stack(session_tag="ward-a")
    _register_condition_write(stack)
    pend = stack.call("condition.record", actor="triage-llm",
                      args={"subject": "pax-sim-0001"},
                      host_can_render=True)
    assert pend["status"] == "pending"
    assert "Malaria" in pend["rendering"]

    done = stack.decide(pend["proposal_id"], "approved",
                        reviewer="dr-sim-ada")
    assert done["committed"] is True
    assert done["verified_by"] == "read-back"

    committed = stack.store.read(done["resource_id"])
    systems = [c.get("system") for c in committed["meta"]["security"]]
    assert ("http://terminology.hl7.org/CodeSystem/"
            "v3-ObservationValue") in systems
    tags = committed["meta"]["tag"]
    assert any(t.get("code") == "ai-authored" for t in tags)
    assert any(t.get("code") == "ward-a" for t in tags)

    prov = stack.store.read(done["provenance_id"])
    who = [a["who"]["display"] for a in prov["agent"]]
    assert "agent:triage-llm" in who
    assert "reviewer:dr-sim-ada" in who


def test_fhirguard_denies_invented_code_system_with_repair_detail():
    stack = _seeded_stack()
    stack.register_write(
        "observation.record",
        description="Propose an Observation for human approval",
        required_args=("subject",),
        builder=lambda args: {"resource": dict(INVENTED_CODE_OBS),
                              "rendering": "Record observation XYZ"})
    out = stack.call("observation.record", actor="triage-llm",
                     args={"subject": "pax-sim-0001"},
                     host_can_render=True)
    assert out["status"] == "policy-denied"
    assert out["committed"] is False
    # audited reason is static configuration text
    assert out["reason"] == agentwrite.FHIRGUARD_DENIAL
    # the agent gets the repairable field-level detail
    assert "not recognised" in out["problems"]
    # nothing reached the store beyond the seeded Patient
    assert stack.store.by_type("Observation") == []


def test_capability_gating_hides_and_refuses_writes_on_degraded_host():
    stack = _seeded_stack()
    _register_allergy_read(stack)
    _register_condition_write(stack)
    listed = stack.list_tools(actor="triage-llm", host_can_render=False)
    assert [t["name"] for t in listed] == ["patient.allergy_search"]
    listed_full = stack.list_tools(actor="triage-llm",
                                   host_can_render=True)
    assert len(listed_full) == 2
    out = stack.call("condition.record", actor="triage-llm",
                     args={"subject": "pax-sim-0001"},
                     host_can_render=False)
    assert out["status"] == "error"
    assert "cannot render" in out["errors"][0]
    assert stack.capabilities(host_can_render=False)["write"] is False


def test_approval_without_named_reviewer_refuses():
    stack = _seeded_stack()
    _register_condition_write(stack)
    pend = stack.call("condition.record", actor="triage-llm",
                      args={"subject": "pax-sim-0001"},
                      host_can_render=True)
    with pytest.raises(Exception, match="named reviewer"):
        stack.decide(pend["proposal_id"], "approved")


def test_denial_attributes_to_reviewer_and_commits_nothing():
    stack = _seeded_stack()
    _register_condition_write(stack)
    pend = stack.call("condition.record", actor="triage-llm",
                      args={"subject": "pax-sim-0001"},
                      host_can_render=True)
    out = stack.decide(pend["proposal_id"], "denied",
                       reviewer="dr-sim-ada")
    assert out["attributed_to"] == "reviewer"
    assert stack.store.by_type("Condition") == []


def test_write_tool_without_builder_errors_loudly():
    stack = _seeded_stack()
    stack.registry.register("ghost.write",
                            description="a tool with no builder",
                            risk="write", required_args=(),
                            fn=lambda a: None)
    out = stack.call("ghost.write", actor="triage-llm", args={},
                     host_can_render=True)
    assert out["status"] == "error"
    assert "no builder" in out["errors"][0]


# ---------------------------------------------------------- coverage


def test_coverage_fraction_and_denominator():
    stack = _seeded_stack()
    report = stack.coverage()
    assert report["fraction"] == "27/27"
    assert report["denominator"] == "us-core-9.0.0"
    assert report["gaps"] == []


# ------------------------------------------------------------- audit


def test_all_three_audit_chains_verify_after_full_day():
    stack = _seeded_stack(session_tag="ward-a")
    _register_allergy_read(stack)
    _register_condition_write(stack)
    stack.call("patient.allergy_search", actor="triage-llm",
               args={"subject": "pax-sim-0001"})
    pend = stack.call("condition.record", actor="triage-llm",
                      args={"subject": "pax-sim-0001"},
                      host_can_render=True)
    stack.decide(pend["proposal_id"], "approved", reviewer="dr-sim-ada")
    stack.coverage()
    assert stack.verify_audit() is True


def test_registration_contracts_refuse_bad_tools():
    stack = _seeded_stack()
    with pytest.raises(AgentWriteError):
        stack.register_read("bad", description="d", store_name="s",
                            query=None)
    with pytest.raises(AgentWriteError):
        stack.register_write("bad2", description="d",
                             required_args=(), builder=None)
