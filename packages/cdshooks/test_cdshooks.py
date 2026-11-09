"""test_cdshooks.py — CDS Hooks service layer."""

import pytest

from cdshooks import (CDSError, CDSRegistry, make_card)


VITALS = {"rr": 24, "spo2": 91, "temp_c": 38.9, "sbp": 95, "hr": 118,
          "avpu": "A", "on_oxygen": True}
ORDER = {"drug": "ibuprofen", "current_meds": ["warfarin"],
         "allergies": ["penicillin"]}


@pytest.fixture()
def registry():
    reg = CDSRegistry()
    reg.load_builtins(proposer="dev", approver="cmo")
    return reg


def test_card_contract():
    with pytest.raises(CDSError, match="without a summary"):
        make_card(summary="", indicator="info", source_label="x")
    with pytest.raises(CDSError, match="unknown indicator"):
        make_card(summary="s", indicator="red", source_label="x")
    with pytest.raises(CDSError, match="without a source"):
        make_card(summary="s", indicator="info", source_label="")


def test_four_eyes_and_discovery():
    reg = CDSRegistry()
    reg.load_builtins(proposer="dev", approver="cmo")
    disc = reg.discovery()
    assert len(disc["services"]) == 3
    ids = {s["id"] for s in disc["services"]}
    assert "news2-early-warning" in ids
    # unratified service is invisible and refuses to run
    reg2 = CDSRegistry()
    svc = dict(id="x", hook="patient-view", title="t",
               prefetch={"a": "b"}, fn=lambda c, p: [])
    reg2.propose(svc, proposer="dev")
    with pytest.raises(CDSError, match="never ratifies"):
        reg2.ratify("x", approver="dev")
    assert reg2.discovery()["services"] == []
    with pytest.raises(CDSError, match="nobody approved"):
        reg2.call("x", hook="patient-view", context={}, prefetch={"a": 1})


def test_unknown_hook_refused():
    reg = CDSRegistry()
    with pytest.raises(CDSError, match="unknown hook"):
        reg.propose({"id": "y", "hook": "on-tuesday", "title": "t",
                     "prefetch": {"a": "b"}, "fn": lambda c, p: []},
                    proposer="dev")


def test_news2_service(registry):
    out = registry.call("news2-early-warning", hook="patient-view",
                        context={"patient": "p1"},
                        prefetch={"vitals": VITALS}, caller="viewer")
    card = out["cards"][0]
    assert card["indicator"] == "critical"
    assert "NEWS2" in card["summary"]
    assert card["source"]["label"].startswith("news2")


def test_prefetch_contract(registry):
    with pytest.raises(CDSError, match="prefetch contract unmet"):
        registry.call("news2-early-warning", hook="patient-view",
                      context={}, prefetch={}, caller="tester")
    trail = registry.audit_trail()
    assert trail[-1]["event"] == "call-refused"


def test_hook_mismatch(registry):
    with pytest.raises(CDSError, match="hook mismatch"):
        registry.call("news2-early-warning", hook="order-sign",
                      context={}, prefetch={"vitals": VITALS})


def test_interaction_and_renal(registry):
    out = registry.call("drug-interaction-check", hook="order-sign",
                        context={"patient": "p1"},
                        prefetch={"order": ORDER})
    assert out["cards"]  # ibuprofen + warfarin must say something
    renal = registry.call("renal-dosing-check", hook="order-sign",
                          context={"patient": "p1"},
                          prefetch={"order": {"drug": "metformin"},
                                    "egfr": 25})
    assert renal["cards"][0]["summary"]
    unrated = registry.call("renal-dosing-check", hook="order-sign",
                            context={"patient": "p1"},
                            prefetch={"order": {"drug": "imaginarycillin"},
                                      "egfr": 90})
    assert "unrated is not clearance" in unrated["cards"][0]["summary"]


def test_adapter_failure_is_loud(registry):
    with pytest.raises(CDSError, match="failed"):
        registry.call("news2-early-warning", hook="patient-view",
                      context={},
                      prefetch={"vitals": {"rr": 20}},  # incomplete
                      caller="viewer")
    assert registry.audit_trail()[-1]["event"] == "call-failed"


def test_audit_chain_verifies(registry):
    registry.call("news2-early-warning", hook="patient-view",
                  context={}, prefetch={"vitals": VITALS})
    assert registry.verify_audit()
    registry.audit_trail()[0]["cards"] = 99  # tamper the copy
    trail = registry._audit
    trail[0]["cards"] = 99
    assert not registry.verify_audit()
