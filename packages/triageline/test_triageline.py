"""Tests for triageline — the citizen triage line."""

import pytest

from triageline import (Error, ProtocolRegistry, TriageLine, VERSION,
                        STARTER_PROTOCOLS)


@pytest.fixture
def line():
    return TriageLine()


class TestProtocolRegistry:
    def test_starter_protocols_are_live(self):
        reg = ProtocolRegistry()
        assert reg.live("chest-pain")["version"] == "chest-pain@1"

    def test_propose_then_ratify_four_eyes(self):
        reg = ProtocolRegistry()
        reg.propose("headache", {"version": "headache@1", "discriminators": {
            "thunderclap": ("emergency", "dispatch-emergency"),
            "mild": ("routine", "self-care-advice")}}, by="dr-a", day=1)
        with pytest.raises(Error, match="proposes never approves"):
            reg.ratify("headache", by="dr-a", day=1)
        reg.ratify("headache", by="dr-b", day=1)
        assert reg.status["headache"] == "live"

    def test_unratified_protocol_refuses_calls(self):
        reg = ProtocolRegistry()
        reg.propose("rash", {"version": "rash@1", "discriminators": {
            "mild": ("routine", "self-care-advice")}}, by="dr-a", day=1)
        line = TriageLine(reg)
        with pytest.raises(Error, match="no live protocol"):
            line.open_call("C-1", "caller-1", handler="nurse-1",
                           minute=60, day=1, stream="rash")

    def test_malformed_protocol_refuses(self):
        reg = ProtocolRegistry()
        with pytest.raises(Error, match="discriminators"):
            reg.propose("x", {"version": "x@1", "discriminators": {
                "bad": ("whenever", "somewhere")}}, by="dr-a", day=1)


class TestCallFlow:
    def test_full_emergency_flow(self, line):
        line.open_call("C-1", "caller-1", handler="nurse-1",
                       minute=540, day=1, stream="chest-pain")
        urgency, disp = line.assess("C-1", "crushing-with-breathlessness",
                                    by="nurse-1", day=1, minute=543)
        assert (urgency, disp) == ("emergency", "dispatch-emergency")
        with pytest.raises(Error, match="handoff"):
            line.complete("C-1", by="nurse-1", day=1, minute=544)
        call = line.complete("C-1", by="nurse-1", day=1, minute=547,
                             handed_to="ambulance-dispatch-lagos")
        assert call["dispatch_met"] is True

    def test_dispatch_breach_is_computed(self, line):
        line.open_call("C-2", "caller-2", handler="nurse-1",
                       minute=540, day=1, stream="chest-pain")
        line.assess("C-2", "crushing-with-breathlessness", by="nurse-1",
                    day=1, minute=541)
        call = line.complete("C-2", by="nurse-1", day=1, minute=560,
                             handed_to="ambulance-dispatch-lagos")
        assert call["dispatch_met"] is False
        assert line.sla_report(1, 600)["dispatch_breaches"] == 1

    def test_unknown_discriminator_refuses(self, line):
        line.open_call("C-3", "caller-3", handler="nurse-1",
                       minute=540, day=1, stream="fever-adult")
        with pytest.raises(Error, match="refuses"):
            line.assess("C-3", "vibes", by="nurse-1", day=1, minute=541)

    def test_handler_assesses_own_call(self, line):
        line.open_call("C-4", "caller-4", handler="nurse-1",
                       minute=540, day=1, stream="fever-adult")
        with pytest.raises(Error, match="own call"):
            line.assess("C-4", "mild-recent", by="nurse-2",
                        day=1, minute=541)

    def test_complete_requires_assessment(self, line):
        line.open_call("C-5", "caller-5", handler="nurse-1",
                       minute=540, day=1, stream="fever-adult")
        with pytest.raises(Error, match="assess"):
            line.complete("C-5", by="nurse-1", day=1, minute=545)

    def test_self_care_states_advice(self, line):
        line.open_call("C-6", "caller-6", handler="nurse-1",
                       minute=540, day=1, stream="fever-adult")
        line.assess("C-6", "mild-recent", by="nurse-1", day=1, minute=541)
        with pytest.raises(Error, match="advice"):
            line.complete("C-6", by="nurse-1", day=1, minute=545)
        call = line.complete("C-6", by="nurse-1", day=1, minute=545,
                             advice_given="fluids, paracetamol, "
                                          "return if worsening")
        assert call["state"] == "completed"


class TestCallbackSLA:
    def _callback_call(self, line, cid="C-10"):
        line.open_call(cid, "caller-x", handler="nurse-1",
                       minute=600, day=1, stream="child-diarrhoea")
        line.assess(cid, "poor-intake", by="nurse-1", day=1, minute=605)
        line.complete(cid, by="nurse-1", day=1, minute=610)

    def test_callback_kept_within_sla(self, line):
        self._callback_call(line)
        cb = line.complete_callback("C-10", by="nurse-3", day=1,
                                    minute=700, outcome="reached, "
                                    "ORS advised")
        assert cb["state"] == "kept"
        report = line.sla_report(1, 800)
        assert report["kept"] == 1 and report["breached"] == 0

    def test_callback_breach_is_stated(self, line):
        self._callback_call(line)
        cb = line.complete_callback("C-10", by="nurse-3", day=1,
                                    minute=900, outcome="reached late")
        assert cb["state"] == "breached"

    def test_overdue_open_promise_is_counted(self, line):
        self._callback_call(line)
        report = line.sla_report(1, 900)
        assert report["overdue_open"] == 1
        assert len(report["sha256"]) == 64

    def test_double_completion_refuses(self, line):
        self._callback_call(line)
        line.complete_callback("C-10", by="nurse-3", day=1,
                               minute=700, outcome="reached")
        with pytest.raises(Error, match="no promised callback"):
            line.complete_callback("C-10", by="nurse-3", day=1,
                                   minute=710, outcome="again")


class TestQA:
    def test_reviewer_never_audits_own_call(self, line):
        line.open_call("C-20", "caller-y", handler="nurse-1",
                       minute=540, day=1, stream="fever-adult")
        line.assess("C-20", "mild-recent", by="nurse-1", day=1,
                    minute=541)
        line.complete("C-20", by="nurse-1", day=1, minute=545,
                      advice_given="rest and fluids")
        with pytest.raises(Error, match="own call"):
            line.qa_review("C-20", reviewer="nurse-1", day=2,
                           verdict="sound")
        line.qa_review("C-20", reviewer="nurse-2", day=2,
                       verdict="sound", note="clean protocol run")
        assert line.calls["C-20"]["qa"]["verdict"] == "sound"

    def test_protocol_version_pinned_on_call(self, line):
        call = line.open_call("C-21", "caller-z", handler="nurse-1",
                              minute=540, day=1, stream="chest-pain")
        assert call["protocol_version"] == STARTER_PROTOCOLS[
            "chest-pain"]["version"]
        assert VERSION.startswith("triageline@")
