"""test_awpconform.py — the conformance suite, run against writegate."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "writegate"))

from awpconform import STATED_LIMITS, VERSION, run_suite
from writegate import MemoryStore, WriteGate


def make_gate():
    return WriteGate(MemoryStore())


def make_policy_gate():
    return WriteGate(MemoryStore(),
                     policies=[lambda r: "paused" if True else None])


def test_suite_passes_writegate():
    report = run_suite(make_gate, make_policy_gate=make_policy_gate,
                       strict=True)
    assert report["ok"], [r for r in report["results"] if not r["ok"]]
    assert report["passed"] == 6  # 5 core + 1 strict
    assert report["suite"] == VERSION
    assert report["attestations"] == list(STATED_LIMITS)


def test_report_states_its_limits():
    report = run_suite(make_gate)
    assert any("scripted reviewer" in a for a in report["attestations"])
    assert any("prompt-injection" in a for a in report["attestations"])


def test_suite_catches_a_broken_gate():
    class BrokenGate:
        """Approves without committing — the classic lie."""
        def __init__(self):
            self.store = MemoryStore()

        def propose(self, resource, rendering, *, agent,
                    host_can_render):
            return {"status": "proposed", "proposal_id": "p1",
                    "committed": False}

        def decide(self, pid, decision, *, reviewer=None):
            # claims commit, writes nothing
            return {"status": "approved", "committed": True,
                    "resource_id": "ghost", "verified_by": "self-report"}

    report = run_suite(BrokenGate)
    assert not report["ok"]
    failed = {r["scenario"] for r in report["results"] if not r["ok"]}
    assert "approve" in failed  # ghost id reads back None
