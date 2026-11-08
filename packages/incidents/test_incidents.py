import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from incidents import (Error, IncidentRegister, IncidentTypeRegistry,  # noqa: E402
                       INCIDENTS_VERSION)


def _register():
    return IncidentRegister()


def _closed_incident(reg):
    iid = reg.report("fall", subject_ref="pat-1", location="ward-3",
                     narrative="found on floor beside bed", by="nurse-a",
                     day=10)
    reg.triage(iid, investigator="quality-officer", by="nurse-b",
               day=10)
    reg.root_cause(iid, factors=["bed rails down", "no call bell"],
                   by="quality-officer", day=11)
    return iid


class TestRegistry:
    def test_reference_types_ratified(self):
        reg = IncidentTypeRegistry()
        assert reg.live("fall")["severity_default"] == "moderate"

    def test_new_type_is_four_eyes(self):
        reg = IncidentTypeRegistry()
        reg.propose("violence", {"severity_default": "moderate"},
                    by="q-officer", day=1)
        with pytest.raises(Error, match="never ratifies"):
            reg.ratify("violence", by="q-officer", day=1)
        reg.ratify("violence", by="cmo", day=1)
        assert reg.live("violence")["status"] == "ratified"

    def test_unregistered_type_refuses(self):
        with pytest.raises(Error, match="not ratified"):
            _register().report("alien-abduction", subject_ref="p",
                               location="w", narrative="x", by="n",
                               day=1)

    def test_no_edit_in_place(self):
        reg = IncidentTypeRegistry()
        with pytest.raises(Error, match="never edit in place"):
            reg.propose("fall", {"severity_default": "mild"}, by="x",
                        day=1)


class TestReporting:
    def test_report_uses_default_severity(self):
        reg = _register()
        iid = reg.report("near-miss", subject_ref="p1",
                         location="pharmacy", narrative="wrong label "
                         "caught at check", by="pharm-a", day=5)
        assert reg.incidents[iid]["severity"] == "no-harm"
        assert reg.incidents[iid]["state"] == "reported"

    def test_empty_narrative_refuses(self):
        with pytest.raises(Error, match="state what happened"):
            _register().report("fall", subject_ref="p", location="w",
                               narrative="  ", by="n", day=1)

    def test_severity_moves_up_only(self):
        reg = _register()
        iid = reg.report("fall", severity="mild", subject_ref="p",
                         location="w", narrative="slip", by="n", day=1)
        reg.regrade(iid, "severe", reason="hip fracture confirmed",
                    by="doctor", day=2)
        assert reg.incidents[iid]["severity"] == "severe"
        with pytest.raises(Error, match="never quietly downgraded"):
            reg.regrade(iid, "mild", reason="feels better", by="d",
                        day=3)


class TestClosureDoctrine:
    def test_full_lifecycle(self):
        reg = _register()
        iid = _closed_incident(reg)
        reg.close(iid, actions=["bed-rail policy amended",
                                "call bells audited"],
                  by="quality-officer", day=12)
        assert reg.incidents[iid]["state"] == "closed"
        assert len(reg.incidents[iid]["history"]) == 3

    def test_reporter_never_closes(self):
        reg = _register()
        iid = _closed_incident(reg)
        with pytest.raises(Error, match="never closes"):
            reg.close(iid, actions=["fixed"], by="nurse-a", day=12)

    def test_close_requires_actions(self):
        reg = _register()
        iid = _closed_incident(reg)
        with pytest.raises(Error, match="corrective action"):
            reg.close(iid, actions=[], by="quality-officer", day=12)

    def test_close_requires_investigation(self):
        reg = _register()
        iid = reg.report("fall", subject_ref="p", location="w",
                         narrative="slip", by="n", day=1)
        with pytest.raises(Error, match="investigated"):
            reg.close(iid, actions=["x"], by="q", day=2)

    def test_root_cause_requires_factors(self):
        reg = _register()
        iid = reg.report("fall", subject_ref="p", location="w",
                         narrative="slip", by="n", day=1)
        reg.triage(iid, investigator="q", by="m", day=1)
        with pytest.raises(Error, match="at least one"):
            reg.root_cause(iid, factors=[], by="q", day=2)


class TestStats:
    def test_stats_are_stated(self):
        reg = _register()
        iid = _closed_incident(reg)
        reg.close(iid, actions=["fixed"], by="quality-officer", day=12)
        reg.report("needlestick", subject_ref="staff-1",
                   location="theatre", narrative="recapping stick",
                   by="nurse-c", day=13)
        s = reg.report_stats()
        assert s["closed"] == 1 and s["open"] == 1
        assert s["by_type"] == {"fall": 1, "needlestick": 1}

    def test_audit_chain_is_checksummed(self):
        reg = _register()
        iid = _closed_incident(reg)
        reg.close(iid, actions=["fixed"], by="quality-officer", day=12)
        assert all(len(a["sha256"]) == 64 for a in reg.audit)

    def test_version(self):
        assert INCIDENTS_VERSION == "incidents@1.0.0"
