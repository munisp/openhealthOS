"""test_writegate.py — approval-gated agent write lifecycle."""

import pytest

from writegate import (AIAST, MemoryStore, WriteGate, WriteGateError)


OBS = {"resourceType": "Observation", "status": "final",
       "code": {"coding": [{"system": "http://loinc.org",
                            "code": "8867-4"}]},
       "subject": {"reference": "Patient/p1"},
       "valueQuantity": {"value": 72, "unit": "beats/minute"}}


@pytest.fixture()
def gate():
    return WriteGate(MemoryStore())


def test_capability_gating(gate):
    assert gate.capabilities(host_can_render=False) == {"write": False}
    assert gate.capabilities(host_can_render=True) == {"write": True}
    with pytest.raises(WriteGateError, match="never offered"):
        gate.propose(OBS, "HR 72", agent="a", host_can_render=False)


def test_proposal_requires_rendering(gate):
    with pytest.raises(WriteGateError, match="blank page"):
        gate.propose(OBS, "", agent="a", host_can_render=True)


def test_full_approve_commit_readback(gate):
    out = gate.propose(OBS, "Heart rate 72 bpm", agent="triage-llm",
                       host_can_render=True)
    assert out["status"] == "proposed" and not out["committed"]
    res = gate.decide(out["proposal_id"], "approved", reviewer="dr-a")
    assert res["committed"] and res["verified_by"] == "read-back"
    stored = gate.store.read(res["resource_id"])
    assert AIAST in stored["meta"]["security"]
    prov = gate.store.read(res["provenance_id"])
    roles = {a["type"]["coding"][0]["code"] for a in prov["agent"]}
    assert roles == {"author", "verifier"}
    assert gate.verify_audit()


def test_denied_persists_nothing(gate):
    out = gate.propose(OBS, "HR 72", agent="a", host_can_render=True)
    res = gate.decide(out["proposal_id"], "denied", reviewer="dr-a")
    assert not res["committed"] and res["attributed_to"] == "reviewer"
    assert gate.store.by_type("Observation") == []


def test_unavailable_is_not_a_denial(gate):
    out = gate.propose(OBS, "HR 72", agent="a", host_can_render=True)
    res = gate.decide(out["proposal_id"], "unavailable")
    assert res["attributed_to"] == "host"  # no human blamed
    assert gate.store.by_type("Observation") == []


def test_approval_needs_named_reviewer(gate):
    out = gate.propose(OBS, "HR 72", agent="a", host_can_render=True)
    with pytest.raises(WriteGateError, match="named reviewer"):
        gate.decide(out["proposal_id"], "approved")


def test_policy_only_denies():
    gate = WriteGate(MemoryStore(),
                     policies=[lambda r: "observation-writes-paused"
                               if r["resourceType"] == "Observation"
                               else None])
    out = gate.propose(OBS, "HR 72", agent="a", host_can_render=True)
    assert out["status"] == "policy-denied" and not out["committed"]
    assert out["reason"] == "observation-writes-paused"  # static text
    assert gate.store.by_type("Observation") == []


def test_policy_failure_denies():
    def boom(r):
        raise RuntimeError("policy engine down")
    gate = WriteGate(MemoryStore(), policies=[boom])
    out = gate.propose(OBS, "HR 72", agent="a", host_can_render=True)
    assert out["status"] == "policy-denied"  # failure fails closed


def test_session_isolation_tag():
    gate = WriteGate(MemoryStore(), session_tag="demo-session-7")
    out = gate.propose(OBS, "HR 72", agent="a", host_can_render=True)
    res = gate.decide(out["proposal_id"], "approved", reviewer="dr-a")
    stored = gate.store.read(res["resource_id"])
    tags = {t["code"] for t in stored["meta"]["tag"]}
    assert "demo-session-7" in tags


def test_audit_tamper_detected(gate):
    out = gate.propose(OBS, "HR 72", agent="a", host_can_render=True)
    gate.decide(out["proposal_id"], "denied", reviewer="dr-a")
    assert gate.verify_audit()
    gate._audit[0]["event"] = "committed"
    assert not gate.verify_audit()
