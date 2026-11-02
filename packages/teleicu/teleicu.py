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


# ---------------------------------------------------------------------
# Bed board: the rounding list is the unit of tele-ICU work.
#
# A census without the day's must-dos is a spreadsheet, not a ward
# round. Every device day is accumulated risk — the rounding list
# surfaces WHO needs WHAT today, most-urgent first, and device-day
# denominators make infection rates real.
ICU_DEVICES = ("ventilator", "central-line", "urinary-catheter",
               "arterial-line")
# check -> device that makes it applicable (None = every ICU patient)
MUST_DOS = {
    "sedation-interruption": "ventilator",
    "spontaneous-breathing-trial": "ventilator",
    "head-of-bed-30": "ventilator",
    "line-necessity-review": "central-line",
    "catheter-necessity-review": "urinary-catheter",
    "dvt-prophylaxis-review": None,
    "family-update": None,
}
CHECK_OUTCOMES = ("done", "contraindicated")
DISCHARGE_OUTCOMES = ("survived", "died", "transferred")
BEDBOARD_VERSION = "icu-bedboard@1.0.0"


class BedBoard:
    """The ICU census with teeth. Doctrine:
      - every open must-do is a named omission — 'contraindicated' is
        a decision with a reason, not a skip
      - device days are counted per patient: infection risk has a
        denominator (rates divide by device days, never by headcount)
      - the rounding list is sorted by what is UNDONE, not by bed
        number — bed order is geography, not priority"""

    def __init__(self, unit):
        if not unit:
            raise TeleReadError("a bed board belongs to a named unit")
        self.unit = unit
        self.patients = {}   # patient_id -> stay record

    def admit(self, patient_id, *, bed, day):
        if patient_id in self.patients and \
                not self.patients[patient_id].get("discharged"):
            raise TeleReadError(f"{patient_id} already on the board — "
                                "a second admission is a transfer, not "
                                "a new patient")
        if not bed:
            raise TeleReadError("no bed — a board entry without a bed "
                                "is a ghost")
        self.patients[patient_id] = {
            "patient_id": patient_id, "bed": bed, "admitted_day": day,
            "movements": [("admitted", day)],
            "devices": [],          # {device, start_day, end_day}
            "checks": {},           # (check, day) -> record
            "discharged": None}
        return dict(self.patients[patient_id])

    def discharge(self, patient_id, *, day, outcome):
        if outcome not in DISCHARGE_OUTCOMES:
            raise TeleReadError(f"unknown discharge outcome: {outcome}")
        p = self._p(patient_id)
        if p["discharged"]:
            raise TeleReadError("already discharged — the board does "
                                "not erase, and it does not double-"
                                "discharge")
        open_devices = [d["device"] for d in p["devices"]
                        if d["end_day"] is None]
        if open_devices:
            raise TeleReadError(
                f"open devices {open_devices} — discharge with a line "
                "unaccounted is how devices walk out in patients")
        p["discharged"] = {"day": day, "outcome": outcome}
        p["movements"].append(("discharged", day))
        return dict(p)

    # ---- devices ----
    def device_start(self, patient_id, device, *, day):
        if device not in ICU_DEVICES:
            raise TeleReadError(f"unknown device: {device}")
        p = self._p(patient_id)
        open_same = [d for d in p["devices"]
                     if d["device"] == device and d["end_day"] is None]
        if open_same:
            raise TeleReadError(f"{device} already running — a second "
                                "start is a data error, not a second "
                                "line")
        p["devices"].append({"device": device, "start_day": day,
                             "end_day": None})
        return dict(p)

    def device_end(self, patient_id, device, *, day):
        p = self._p(patient_id)
        open_same = [d for d in p["devices"]
                     if d["device"] == device and d["end_day"] is None]
        if not open_same:
            raise TeleReadError(f"no open {device} — ending what never "
                                "started is a crossed wire")
        if day < open_same[0]["start_day"]:
            raise TeleReadError("device ended before it started — check "
                                "the clock, don't bend it")
        open_same[0]["end_day"] = day
        return dict(p)

    def _device_days(self, p, device, as_of):
        end_all = p["discharged"]["day"] if p["discharged"] else as_of
        return sum(max(0, (d["end_day"] if d["end_day"] is not None
                           else end_all) - d["start_day"])
                   for d in p["devices"] if d["device"] == device)

    # ---- the day's must-dos ----
    def _applicable(self, p):
        open_devices = {d["device"] for d in p["devices"]
                        if d["end_day"] is None}
        return {chk for chk, dev in MUST_DOS.items()
                if dev is None or dev in open_devices}

    def record_check(self, patient_id, check, *, day, by,
                     outcome="done", reason=None):
        if check not in MUST_DOS:
            raise TeleReadError(f"unknown must-do: {check}")
        p = self._p(patient_id)
        if p["discharged"]:
            raise TeleReadError("discharged — the board is closed")
        if check not in self._applicable(p):
            raise TeleReadError(
                f"{check} is not applicable without an open "
                f"{MUST_DOS[check]} — ticking it anyway is checkbox "
                "medicine")
        if outcome not in CHECK_OUTCOMES:
            raise TeleReadError(f"unknown check outcome: {outcome}")
        if outcome == "contraindicated" and not reason:
            raise TeleReadError(
                "a contraindication without a reason is a silent skip "
                "— 'contraindicated' is a decision, not an omission")
        if not by:
            raise TeleReadError("unsigned checks are no checks")
        if (check, day) in p["checks"]:
            raise TeleReadError("already recorded today — the board is "
                                "not a tally sheet to pad")
        p["checks"][(check, day)] = {"outcome": outcome,
                                     "reason": reason, "by": by}
        return dict(p["checks"][(check, day)])

    def rounding_list(self, as_of):
        """Who needs WHAT today, most-undone first. Open must-dos are
        named per patient — the list answers the only question a ward
        round asks."""
        out = []
        for pid, p in self.patients.items():
            if p["discharged"]:
                continue
            applicable = self._applicable(p)
            open_dos = sorted(
                chk for chk in applicable
                if (chk, as_of) not in p["checks"])
            device_days = {dev: self._device_days(p, dev, as_of)
                           for dev in ICU_DEVICES}
            device_days = {d: n for d, n in device_days.items() if n}
            out.append({"patient_id": pid, "bed": p["bed"],
                        "days_in_unit": as_of - p["admitted_day"],
                        "open_must_dos": open_dos,
                        "device_days": device_days,
                        "n_open": len(open_dos)})
        out.sort(key=lambda r: (-r["n_open"],
                                -max(r["device_days"].values(),
                                     default=0),
                                r["bed"]))
        return out

    def device_report(self, as_of):
        """Device utilization with the denominators infection-control
        actually divides by: a CLABSI rate without central-line-days
        is marketing."""
        per_device = {d: 0 for d in ICU_DEVICES}
        patient_days = 0
        for p in self.patients.values():
            end = p["discharged"]["day"] if p["discharged"] else as_of
            patient_days += max(0, end - p["admitted_day"])
            for dev in ICU_DEVICES:
                per_device[dev] += self._device_days(p, dev, as_of)
        return {"unit": self.unit, "patient_days": patient_days,
                "device_days": {d: n for d, n in per_device.items()
                                if n},
                "utilization": {
                    d: round(n / patient_days, 3)
                    for d, n in per_device.items()
                    if n and patient_days},
                "board_version": BEDBOARD_VERSION,
                "note": "infection rates divide by device days, never "
                        "by patient count"}

    def _p(self, pid):
        if pid not in self.patients:
            raise TeleReadError(f"not on the {self.unit} board: {pid}")
        return self.patients[pid]
