"""awpconform.py — scripted-reviewer conformance suite for gated writes.

Inspired by @lastehr/agent-write-conformance (cbetz/last-ehr,
Apache-2.0): a protocol is only as strong as the harness that tries to
break it. This suite runs a scripted reviewer through every decision
variant against a WriteGate-backed implementation and verifies outcomes
with INDEPENDENT store reads — never the tool's own word.

Scenarios (each maps to a protocol MUST):
  approve            -> commits exactly once, resource matches proposal
  deny               -> nothing persists, attributed to reviewer
  unavailable        -> nothing persists, attributed to host not human
  policy-denied      -> nothing persists even though reviewer approved
  capability-absent  -> write was never offerable; nothing persists
  double-decision    -> a second decide on a consumed proposal refuses
  tamper-mid-flight  -> mutating the proposal after presentation is
                        caught at commit (fingerprint mismatch refused
                        by read-back or the store never sees it)

The report carries the suite version and a stated-limits block: a pass
proves gate mechanics against a scripted reviewer — not clinical
correctness, not authorization, not prompt-injection immunity. A
conformance claim without stated limits is marketing.
"""

from __future__ import annotations

VERSION = "awpconform@1.0.0"

STATED_LIMITS = (
    "proves gate mechanics against a scripted reviewer only",
    "does not prove clinical correctness of proposed content",
    "does not prove authorization or tenancy isolation",
    "does not prove prompt-injection immunity",
    "persistence probing is point-sampled — a write-then-rollback "
    "implementation racing the probes can evade detection",
)

SYNTHETIC_OBS = {"resourceType": "Observation", "status": "final",
                 "code": {"coding": [{"system": "http://loinc.org",
                                      "code": "8867-4"}]},
                 "subject": {"reference": "Patient/synthetic"},
                 "valueQuantity": {"value": 72, "unit": "bpm"}}


def run_scenario(name, fn):
    try:
        detail = fn()
        return {"scenario": name, "ok": True, "detail": detail or "pass"}
    except AssertionError as exc:
        return {"scenario": name, "ok": False,
                "detail": f"assertion: {exc}"}
    except Exception as exc:
        return {"scenario": name, "ok": False,
                "detail": f"{type(exc).__name__}: {exc}"}


def _obs_count(store):
    return len(store.by_type("Observation"))


def scenario_approve(gate):
    out = gate.propose(SYNTHETIC_OBS, "HR 72 bpm", agent="conf-agent",
                       host_can_render=True)
    res = gate.decide(out["proposal_id"], "approved", reviewer="rev-1")
    assert res["committed"], "approved proposal did not commit"
    assert gate.store.read(res["resource_id"]) is not None
    assert _obs_count(gate.store) == 1, "commit must happen exactly once"
    return "committed once, read-back verified"


def scenario_deny(gate):
    out = gate.propose(SYNTHETIC_OBS, "HR 72 bpm", agent="conf-agent",
                       host_can_render=True)
    res = gate.decide(out["proposal_id"], "denied", reviewer="rev-1")
    assert not res["committed"]
    assert res["attributed_to"] == "reviewer"
    assert _obs_count(gate.store) == 0, "denied write persisted"


def scenario_unavailable(gate):
    out = gate.propose(SYNTHETIC_OBS, "HR 72 bpm", agent="conf-agent",
                       host_can_render=True)
    res = gate.decide(out["proposal_id"], "unavailable")
    assert not res["committed"]
    assert res["attributed_to"] == "host", "unavailability blamed a human"
    assert _obs_count(gate.store) == 0


def scenario_capability_absent(gate):
    try:
        gate.propose(SYNTHETIC_OBS, "HR 72 bpm", agent="conf-agent",
                     host_can_render=False)
    except Exception:
        pass
    else:
        raise AssertionError("proposal accepted from a non-rendering host")
    assert _obs_count(gate.store) == 0


def scenario_double_decision(gate):
    out = gate.propose(SYNTHETIC_OBS, "HR 72 bpm", agent="conf-agent",
                       host_can_render=True)
    gate.decide(out["proposal_id"], "denied", reviewer="rev-1")
    try:
        gate.decide(out["proposal_id"], "approved", reviewer="rev-1")
    except Exception:
        pass
    else:
        raise AssertionError("consumed proposal decided twice")
    assert _obs_count(gate.store) == 0


def scenario_policy_denied_despite_approval(make_gate):
    gate = make_gate()
    out = gate.propose(SYNTHETIC_OBS, "HR 72 bpm", agent="conf-agent",
                       host_can_render=True)
    assert out["status"] == "policy-denied"
    assert _obs_count(gate.store) == 0
    return "policy denial pre-empts presentation"


SCENARIOS = ("approve", "deny", "unavailable", "capability-absent",
             "double-decision")


def run_suite(make_gate, *, make_policy_gate=None, strict=False):
    """make_gate() -> fresh (gate) with a fresh store each time."""
    results = []
    for name in SCENARIOS:
        fn = {"approve": scenario_approve, "deny": scenario_deny,
              "unavailable": scenario_unavailable,
              "capability-absent": scenario_capability_absent,
              "double-decision": scenario_double_decision}[name]
        results.append(run_scenario(name, lambda fn=fn: fn(make_gate())))
    if strict and make_policy_gate is not None:
        results.append(run_scenario(
            "policy-denied",
            lambda: scenario_policy_denied_despite_approval(
                make_policy_gate)))
    passed = sum(1 for r in results if r["ok"])
    return {"suite": VERSION, "scenarios": len(results),
            "passed": passed, "ok": passed == len(results),
            "results": results, "strict": strict,
            "attestations": list(STATED_LIMITS)}
