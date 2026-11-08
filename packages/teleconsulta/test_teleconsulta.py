"""Tests for teleconsulta — island-to-hub store-and-forward."""

import pytest

from teleconsulta import Error, TeleconsultaNetwork, SLA_MINUTES, VERSION


@pytest.fixture
def net():
    n = TeleconsultaNetwork()
    n.register_facility("CV-PV-HC", "Health Centre São Vicente",
                        kind="peripheral")
    n.register_facility("CV-HAN", "Hospital Agostinho Neto",
                        kind="hub", specialties=["dermatology",
                                                 "cardiology"])
    return n


def _make(net, cid="TC-1", priority="urgent", specialty="dermatology"):
    net.create(cid, "p-maria", "CV-PV-HC", specialty,
               priority=priority, by="nurse-silva", day=1, minute=480,
               history="pruritic plaque 3 weeks",
               attachments=["img://photo-1"])
    b = net.bundle(cid, by="nurse-silva", day=1, minute=490)
    net.transmit(cid, day=1, minute=495, medium="sync")
    return b


class TestRegistry:
    def test_hub_declares_specialties(self, net):
        with pytest.raises(Error, match="declares"):
            net.register_facility("CV-X", "X", kind="hub")

    def test_unknown_specialty_refuses(self, net):
        with pytest.raises(Error, match="unknown specialties"):
            net.register_facility("CV-Y", "Y", kind="hub",
                                  specialties=["astrology"])

    def test_kind_is_peripheral_or_hub(self, net):
        with pytest.raises(Error, match="peripheral or hub"):
            net.register_facility("CV-Z", "Z", kind="kiosk")


class TestLifecycle:
    def test_full_store_and_forward(self, net):
        b = _make(net)
        net.receive("TC-1", "CV-HAN", bundle_sha256=b["sha256"],
                    day=1, minute=500)
        resp = net.answer("TC-1", specialist="dr-pereira", day=1,
                          minute=800, assessment="psoriasis likely",
                          plan="topical steroid, review 4 weeks")
        assert resp["sla_met"] is True
        assert resp["sla_minutes"] == SLA_MINUTES["urgent"]
        net.close("TC-1", by="nurse-silva", day=2, minute=480,
                  outcome="treated locally")
        assert net.consults["TC-1"]["state"] == "closed"

    def test_history_is_mandatory(self, net):
        with pytest.raises(Error, match="history"):
            net.create("TC-2", "p-x", "CV-PV-HC", "dermatology",
                       priority="routine", by="n", day=1, minute=1,
                       history="")

    def test_hub_cannot_originate(self, net):
        with pytest.raises(Error, match="peripheral"):
            net.create("TC-3", "p-x", "CV-HAN", "cardiology",
                       priority="routine", by="dr", day=1, minute=1,
                       history="chest pain")

    def test_only_requester_seals_bundle(self, net):
        net.create("TC-4", "p-x", "CV-PV-HC", "dermatology",
                   priority="routine", by="nurse-silva", day=1,
                   minute=1, history="rash")
        with pytest.raises(Error, match="own bundle"):
            net.bundle("TC-4", by="nurse-other", day=1, minute=2)

    def test_checksum_mismatch_refuses_at_hub(self, net):
        _make(net)
        with pytest.raises(Error, match="checksum mismatch"):
            net.receive("TC-1", "CV-HAN", bundle_sha256="0" * 64,
                        day=1, minute=500)

    def test_hub_without_specialty_refuses(self, net):
        net.register_facility("CV-HBS", "Hospital Baptista de Sousa",
                              kind="hub", specialties=["radiology"])
        _make(net)
        with pytest.raises(Error, match="does not cover"):
            net.receive("TC-1", "CV-HBS", bundle_sha256="0" * 64,
                        day=1, minute=500)

    def test_answer_requires_plan(self, net):
        b = _make(net)
        net.receive("TC-1", "CV-HAN", bundle_sha256=b["sha256"],
                    day=1, minute=500)
        with pytest.raises(Error, match="management plan"):
            net.answer("TC-1", specialist="dr-p", day=1, minute=800,
                       assessment="eczema", plan="")

    def test_answer_requires_receive_first(self, net):
        _make(net)
        with pytest.raises(Error, match="receives before"):
            net.answer("TC-1", specialist="dr-p", day=1, minute=800,
                       assessment="x", plan="y")


class TestSLA:
    def test_breach_is_computed(self, net):
        b = _make(net, cid="TC-10", priority="emergency")
        net.receive("TC-10", "CV-HAN", bundle_sha256=b["sha256"],
                    day=1, minute=500)
        resp = net.answer("TC-10", specialist="dr-p", day=1,
                          minute=480 + 120, assessment="x", plan="y")
        assert resp["sla_met"] is False  # 120 > 60 emergency SLA
        assert net.sla_report()["sla_breached"] == 1

    def test_deferred_medium_is_a_state(self, net):
        net.create("TC-11", "p-x", "CV-PV-HC", "dermatology",
                   priority="routine", by="n", day=1, minute=1,
                   history="chronic ulcer")
        net.bundle("TC-11", by="n", day=1, minute=2)
        net.transmit("TC-11", day=3, minute=1, medium="boat")
        assert net.consults["TC-11"]["events"][-1]["medium"] == "boat"


class TestEscalation:
    def test_escalation_names_channel_and_reason(self, net):
        b = _make(net)
        net.receive("TC-1", "CV-HAN", bundle_sha256=b["sha256"],
                    day=1, minute=500)
        with pytest.raises(Error, match="reason"):
            net.escalate("TC-1", by="dr-p", day=1, minute=510,
                         channel="medevac", reason="")
        net.escalate("TC-1", by="dr-p", day=1, minute=510,
                     channel="medevac",
                     reason="necrotising fasciitis suspected")
        assert net.consults["TC-1"]["state"] == "escalated"

    def test_unknown_channel_refuses(self, net):
        b = _make(net)
        net.receive("TC-1", "CV-HAN", bundle_sha256=b["sha256"],
                    day=1, minute=500)
        with pytest.raises(Error, match="channel"):
            net.escalate("TC-1", by="dr-p", day=1, minute=510,
                         channel="whatsapp", reason="x")

    def test_close_requires_outcome_and_state(self, net):
        _make(net)
        with pytest.raises(Error, match="answered or escalated"):
            net.close("TC-1", by="n", day=1, minute=600, outcome="x")

    def test_sla_report_shape(self, net):
        b = _make(net)
        net.receive("TC-1", "CV-HAN", bundle_sha256=b["sha256"],
                    day=1, minute=500)
        net.answer("TC-1", specialist="dr-p", day=1, minute=600,
                   assessment="x", plan="y")
        r = net.sla_report()
        assert r["consults"] == 1 and r["answered"] == 1
        assert r["sla_met"] == 1 and r["version"] == VERSION
        assert len(r["sha256"]) == 64
