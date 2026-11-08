"""Tests for sharedsum — national shared patient summary."""

import pytest

from sharedsum import (Error, SharedSummaryService, SummaryContribution,
                       VERSION)


@pytest.fixture
def svc():
    s = SharedSummaryService()
    s.contribute(SummaryContribution(
        "p-ada", "NG-LUTH", "problems", 10,
        [{"code": "B54", "display": "Malaria"}],
        contributed_by="dr-okafor"))
    s.contribute(SummaryContribution(
        "p-ada", "NG-UCH", "allergies", 12,
        [{"substance": "artemisinin", "reaction": "urticaria"}],
        contributed_by="dr-bello"))
    s.contribute(SummaryContribution(
        "p-ada", "NG-LUTH", "problems", 15,
        [{"code": "I10", "display": "Hypertension"}],
        contributed_by="dr-okafor"))
    return s


class TestContribution:
    def test_contribution_requires_provenance(self):
        with pytest.raises(Error, match="contributor"):
            SummaryContribution("p-x", "F", "problems", 1,
                                [{"code": "A00"}], contributed_by="")

    def test_unknown_section_refuses(self):
        with pytest.raises(Error, match="section"):
            SummaryContribution("p-x", "F", "gossip", 1,
                                [{"code": "A00"}], contributed_by="dr-a")

    def test_empty_contribution_refuses(self):
        with pytest.raises(Error, match="noise"):
            SummaryContribution("p-x", "F", "problems", 1, [],
                                contributed_by="dr-a")

    def test_provenance_travels(self, svc):
        out = svc.read("p-ada", accessor="dr-c", facility="NG-AKTH",
                       day=20, context="care-relationship")
        assert out["sections"]["problems"][0]["facility"] == "NG-LUTH"
        assert out["sections"]["allergies"][0]["facility"] == "NG-UCH"


class TestAccess:
    def test_context_is_mandatory_and_known(self, svc):
        with pytest.raises(Error, match="context"):
            svc.read("p-ada", accessor="dr-c", facility="F", day=1,
                     context="curiosity")

    def test_reader_is_named(self, svc):
        with pytest.raises(Error, match="reader"):
            svc.read("p-ada", accessor="", facility="F", day=1,
                     context="care-relationship")

    def test_break_glass_requires_reason(self, svc):
        with pytest.raises(Error, match="reason"):
            svc.read("p-ada", accessor="dr-c", facility="F", day=1,
                     context="break-glass")

    def test_break_glass_reads_and_logs(self, svc):
        out = svc.read("p-ada", accessor="dr-c", facility="NG-AKTH",
                       day=20, context="break-glass",
                       reason="unconscious in ED")
        assert "problems" in out["sections"]
        bg = svc.break_glass_report(20)
        assert len(bg) == 1
        assert bg[0]["break_glass_reason"] == "unconscious in ED"

    def test_payload_is_checksummed(self, svc):
        out = svc.read("p-ada", accessor="dr-c", facility="F", day=1,
                       context="care-relationship")
        assert len(out["sha256"]) == 64
        assert out["version"] == VERSION


class TestRestriction:
    def test_restrict_hides_from_care_relationship(self, svc):
        svc.restrict("p-ada", by="p-ada", day=18)
        with pytest.raises(Error, match="restricted"):
            svc.read("p-ada", accessor="dr-c", facility="F", day=19,
                     context="care-relationship")

    def test_refusal_is_logged(self, svc):
        svc.restrict("p-ada", by="p-ada", day=18)
        with pytest.raises(Error):
            svc.read("p-ada", accessor="dr-c", facility="F", day=19,
                     context="care-relationship")
        log = svc.citizen_audit("p-ada", by="p-ada")
        assert any(e["event"] == "read-refused-restricted"
                   for e in log)

    def test_only_citizen_restricts(self, svc):
        with pytest.raises(Error, match="citizen"):
            svc.restrict("p-ada", by="dr-c", day=18)

    def test_named_section_scope_survives_break_glass(self, svc):
        svc.restrict("p-ada", by="p-ada", day=18, scope="problems")
        out = svc.read("p-ada", accessor="dr-c", facility="F", day=19,
                       context="break-glass", reason="ED emergency")
        assert "problems" not in out["sections"]
        assert "allergies" in out["sections"]

    def test_lift_restriction(self, svc):
        svc.restrict("p-ada", by="p-ada", day=18)
        svc.lift_restriction("p-ada", by="p-ada", day=19)
        out = svc.read("p-ada", accessor="dr-c", facility="F", day=20,
                       context="care-relationship")
        assert "problems" in out["sections"]


class TestAccountability:
    def test_citizen_audit_is_theirs(self, svc):
        svc.read("p-ada", accessor="dr-c", facility="F", day=1,
                 context="care-relationship")
        with pytest.raises(Error, match="citizen"):
            svc.citizen_audit("p-ada", by="dr-c")
        log = svc.citizen_audit("p-ada", by="p-ada")
        assert len(log) == 1 and log[0]["accessor"] == "dr-c"

    def test_coverage_counts(self, svc):
        svc.restrict("p-ada", by="p-ada", day=18)
        cov = svc.coverage()
        assert cov == {"patients": 1, "contributions": 3,
                       "restricted": 1, "reads": 0, "version": VERSION}
