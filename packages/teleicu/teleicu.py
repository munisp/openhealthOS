"""teleicu.py — remote critical-care oversight.

The trigger engine fires; someone scarce must watch. A deteriorating
patient at an EmONC facility becomes an oversight case: a remote
intensivist acknowledges, drafts a consult note, and the facility nurse
confirms which actions were actually TAKEN — because a recommendation
nobody acted on is documentation, not care.

Doctrine: hub silence is not patient stability; unacknowledged
deterioration re-escalates; response time is measured with denominators.
"""

from teleread import DomainConfig, ReadingService, TeleReadError

CONFIG = DomainConfig(
    "teleicu",
    scopes=("intensivist",),
    critical_findings=("imminent-arrest",),
    artifact_kinds=("vitals-stream",),
    sla_hours={"stat": 1, "urgent": 4, "routine": 24})


class TeleICU:
    def __init__(self, readers):
        self.reading = ReadingService(CONFIG, readers)
        self.oversight = {}

    def open_oversight(self, case_id, *, patient_id, facility, trigger_ref,
                       day, severity="urgent"):
        if not trigger_ref:
            raise TeleReadError(
                "oversight opens from a trigger reference — remote "
                "watching without a signal is voyeurism, not care")
        if severity not in CONFIG.sla_hours:
            raise TeleReadError(f"unknown severity: {severity}")
        self.oversight[case_id] = {
            "patient_id": patient_id, "trigger_ref": trigger_ref,
            "opened_day": day, "actions_confirmed": []}
        return self.reading.order(
            case_id, priority=severity,
            question=f"deterioration oversight (trigger {trigger_ref})",
            facility=facility, referrer="trigger-engine", day=day,
            artifact="vitals-stream",
            meta={"patient_id": patient_id})

    def consult_note(self, case_id, *, intensivist, day, assessment,
                     actions):
        t = self.reading._case(case_id)
        if t["assigned_to"] != intensivist:
            raise TeleReadError("only the assigned intensivist consults")
        if not actions:
            raise TeleReadError("a consult note without actions is an "
                                "essay, not an intervention")
        self.oversight[case_id]["note"] = {
            "by": intensivist, "day": day, "assessment": assessment,
            "actions": list(actions)}
        return dict(self.oversight[case_id]["note"])

    def confirm_action(self, case_id, *, action, day, by):
        """Facility staff confirm what was ACTUALLY done."""
        o = self._ov(case_id)
        if "note" not in o:
            raise TeleReadError("no consult note to act on")
        if action not in o["note"]["actions"]:
            raise TeleReadError(
                "confirming an action the intensivist never recommended "
                "— mismatch is a finding, not a tick-box")
        o["actions_confirmed"].append({"action": action, "day": day,
                                       "by": by})
        return dict(o)

    def execution_gap(self, case_id):
        o = self._ov(case_id)
        if "note" not in o:
            raise TeleReadError("no consult note yet")
        recommended = o["note"]["actions"]
        done = {a["action"] for a in o["actions_confirmed"]}
        return {"recommended": len(recommended),
                "confirmed": len(done),
                "outstanding": [a for a in recommended if a not in done],
                "note": "outstanding actions are the gap between advice "
                        "and care"}

    def _ov(self, cid):
        if cid not in self.oversight:
            raise TeleReadError(f"unknown oversight case: {cid}")
        return self.oversight[cid]
