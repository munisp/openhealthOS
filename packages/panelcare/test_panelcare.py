"""Tests for panelcare — primary-care panel management."""

import pytest

from panelcare import (Error, Panel, PanelRegistry, VERSION,
                       RECALL_INTERVALS)


@pytest.fixture
def panel():
    p = Panel("dr-okafor", "NG-PHC-SUR", cap=3)
    p.assign("p-ada", day=0, sex="female", birth_day=-10000,
             conditions=["hypertension"])
    p.assign("p-bola", day=0, sex="male", birth_day=-500,
             conditions=["hiv-art"])
    return p


class TestAssignment:
    def test_capacity_refuses_naming_cap(self, panel):
        panel.assign("p-c", day=1)
        with pytest.raises(Error, match="at capacity \\(3\\)"):
            panel.assign("p-d", day=1)

    def test_duplicate_assignment_refuses(self, panel):
        with pytest.raises(Error, match="already on this panel"):
            panel.assign("p-ada", day=1)

    def test_unknown_condition_refuses(self, panel):
        with pytest.raises(Error, match="unknown chronic condition"):
            panel.assign("p-c", day=1, conditions=["gout"])

    def test_transfer_is_reasoned_and_recorded(self):
        reg = PanelRegistry("NG-PHC-SUR")
        a = reg.open_panel("dr-a", cap=10)
        b = reg.open_panel("dr-b", cap=10)
        a.assign("p-x", day=0, conditions=["asthma"])
        with pytest.raises(Error, match="reason"):
            a.transfer_out("p-x", to_panel=b, day=5, by="admin", reason="")
        a.transfer_out("p-x", to_panel=b, day=5, by="admin",
                       reason="moved to b's catchment")
        assert reg.find_panel("p-x") == "dr-b"
        assert b.members["p-x"]["moves"][0]["reason"] == \
            "moved to b's catchment"


class TestRecalls:
    def test_overdue_is_computed(self, panel):
        # hiv-art interval 90d from assignment day 0; at day 100 overdue by 10
        overdue = panel.overdue_recalls(100)
        hiv = [o for o in overdue if o["condition"] == "hiv-art"]
        assert hiv[0]["days_overdue"] == 10
        assert hiv[0]["never_reviewed"] is True

    def test_review_resets_clock(self, panel):
        panel.record_review("p-bola", "hiv-art", day=80, by="dr-okafor")
        assert panel.overdue_recalls(100) == [] or all(
            o["patient_ref"] != "p-bola" or o["condition"] != "hiv-art"
            for o in panel.overdue_recalls(100))
        # hypertension (180d) not yet due at day 100
        assert all(o["condition"] != "hypertension"
                   for o in panel.overdue_recalls(100))

    def test_review_requires_condition_on_list(self, panel):
        with pytest.raises(Error, match="not on this patient's list"):
            panel.record_review("p-ada", "hiv-art", day=1, by="dr-okafor")

    def test_overdue_counts_from_assignment_when_never_reviewed(self,
                                                                panel):
        interval = RECALL_INTERVALS["hypertension"]
        overdue = panel.overdue_recalls(interval + 5)
        htn = [o for o in overdue if o["condition"] == "hypertension"]
        assert htn[0]["days_overdue"] == 5


class TestPrevention:
    def test_due_list_by_age_and_sex(self, panel):
        # p-ada: age day 10000 - (-10000)... birth_day -10000, day 0 ->
        # age 10000d ≈ 27y female: childhood no, cervical yes (9125+)
        due = panel.prevention_due("p-ada", 0)
        progs = {d["programme"]: d["status"] for d in due}
        assert progs.get("cervical-screening") == "due"
        assert "childhood-immunization" not in progs

    def test_child_gets_immunization_due(self, panel):
        due = panel.prevention_due("p-bola", 0)  # age 500d
        progs = {d["programme"]: d["status"] for d in due}
        assert progs.get("childhood-immunization") == "due"
        assert "cervical-screening" not in progs  # male

    def test_unknown_age_is_stated_not_assumed(self, panel):
        panel.assign("p-c", day=1)  # no birth_day
        due = panel.prevention_due("p-c", 100)
        assert all("stated" in d["status"] for d in due)


class TestKPIs:
    def test_kpis_are_derived(self, panel):
        panel.record_review("p-ada", "hypertension", day=10,
                            by="dr-okafor")
        k = panel.kpis(100)
        assert k["panel_size"] == 2 and k["cap"] == 3
        assert k["chronic_conditions"] == 2
        assert k["chronic_reviewed_at_least_once"] == 1
        # day 100: hiv-art (90d from day 0) overdue; hypertension
        # reviewed day 10 (100-10=90 <= 180) not overdue
        assert k["overdue_recalls"] == 1
        assert k["never_reviewed"] == 1
        assert len(k["sha256"]) == 64 and k["version"] == VERSION

    def test_unit_report_states_unassigned(self, panel):
        reg = PanelRegistry("NG-PHC-SUR")
        reg.panels["dr-okafor"] = panel
        with pytest.raises(Error, match="reason"):
            reg.declare_unassigned("p-new", day=1, reason="")
        reg.declare_unassigned("p-new", day=1,
                               reason="new resident, awaiting panel")
        report = reg.unit_report(100)
        assert report["citizens_on_panels"] == 2
        assert report["declared_unassigned"] == 1
        assert len(report["sha256"]) == 64

    def test_find_panel_states_absence(self, panel):
        reg = PanelRegistry("NG-PHC-SUR")
        reg.panels["dr-okafor"] = panel
        assert reg.find_panel("p-ada") == "dr-okafor"
        assert reg.find_panel("p-ghost") is None
