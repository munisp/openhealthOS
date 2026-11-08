"""panelcare.py — primary-care panel management (the SClínico CSP pattern).

Primary care is a list business: every citizen belongs on a named
clinician's panel, every chronic patient is owed a recall, every child
is owed their vaccines, and the unit's quality is the sum of promises
kept. A panel nobody measures is a filing cabinet, not a service.

Doctrine:
- EVERY CITIZEN IS ON A PANEL OR STATED UNASSIGNED: assignment is
  capacity-bounded; an over-capacity panel refuses, naming the cap.
- RECALLS ARE OBLIGATIONS WITH DATES: a chronic-disease review is due
  on a day; overdue is computed, never estimated.
- PREVENTION IS A DUE-LIST, NOT A CAMPAIGN POSTER: immunization and
  screening gaps are listed per patient with the programme that owes
  them.
- PANEL MOVES ARE REASONED: transfers name the receiving clinician and
  the reason; a silent reassignment is an abduction.
- KPIs ARE DERIVED, NOT DECLARED: coverage, chronic recall currency,
  and prevention currency are computed from the panel, not typed into
  a report.
"""

import hashlib
import json

VERSION = "panelcare@1.0.0"

DEFAULT_PANEL_CAP = 1800  # citizens per clinician; a national policy value

# Programme recall intervals, in days. Entries, not branches.
RECALL_INTERVALS = {
    "hypertension": 180,
    "diabetes-t2": 180,
    "asthma": 180,
    "hiv-art": 90,
    "epilepsy": 180,
}

# Preventive programmes with the age window (days) they apply to.
# Age in days keeps the engine calendar-free.
PREVENTION_PROGRAMMES = {
    "childhood-immunization": {"min_age_days": 0, "max_age_days": 1825},
    "cervical-screening": {"min_age_days": 9125, "max_age_days": 23725,
                           "sex": "female"},
    "hypertension-screen": {"min_age_days": 14600, "max_age_days": None},
}


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class Panel:
    """One clinician's list of citizens."""

    def __init__(self, clinician, facility, cap=DEFAULT_PANEL_CAP):
        if not clinician:
            raise Error("a panel names its clinician")
        if cap < 1:
            raise Error("a panel cap is positive")
        self.clinician = clinician
        self.facility = facility
        self.cap = cap
        self.members = {}  # patient_ref -> member record

    def assign(self, patient_ref, *, day, sex=None, birth_day=None,
               conditions=()):
        if patient_ref in self.members:
            raise Error(f"{patient_ref} already on this panel")
        if len(self.members) >= self.cap:
            raise Error(f"panel at capacity ({self.cap}); assignment "
                        "refused — an over-capacity panel is unsafe, "
                        "not ambitious")
        conditions = list(conditions)
        for c in conditions:
            if c not in RECALL_INTERVALS:
                raise Error(f"unknown chronic condition {c!r}; "
                            f"conditions are {tuple(RECALL_INTERVALS)}")
        self.members[patient_ref] = {
            "patient_ref": patient_ref, "assigned_day": day,
            "sex": sex, "birth_day": birth_day,
            "conditions": conditions, "recalls": {},
        }

    def transfer_out(self, patient_ref, *, to_panel, day, by, reason):
        """A move names receiver and reason; both panels record it."""
        if patient_ref not in self.members:
            raise Error(f"{patient_ref} not on this panel")
        if not reason:
            raise Error("a panel transfer carries a stated reason")
        member = self.members.pop(patient_ref)
        to_panel.members[patient_ref] = member
        member.setdefault("moves", []).append({
            "from": self.clinician, "to": to_panel.clinician,
            "day": day, "by": by, "reason": reason})

    # --- recalls -------------------------------------------------------
    def record_review(self, patient_ref, condition, *, day, by, note=""):
        member = self._member(patient_ref)
        if condition not in member["conditions"]:
            raise Error(f"{condition} is not on this patient's list")
        member["recalls"][condition] = {"last_review_day": day,
                                        "by": by, "note": note}

    def overdue_recalls(self, day):
        """Computed, never estimated: each condition owes a review every
        RECALL_INTERVALS days, counted from assignment if never done."""
        overdue = []
        for ref, m in sorted(self.members.items()):
            for condition in m["conditions"]:
                interval = RECALL_INTERVALS[condition]
                last = m["recalls"].get(condition)
                anchor = last["last_review_day"] if last else \
                    m["assigned_day"]
                days_since = day - anchor
                if days_since > interval:
                    overdue.append({
                        "patient_ref": ref, "condition": condition,
                        "days_overdue": days_since - interval,
                        "never_reviewed": last is None,
                    })
        return overdue

    # --- prevention ------------------------------------------------------
    def prevention_due(self, patient_ref, day):
        """Which programmes owe this patient contact, with the
        programme stated. Age from birth_day; unknown age is stated,
        not assumed."""
        m = self._member(patient_ref)
        due = []
        for prog, spec in sorted(PREVENTION_PROGRAMMES.items()):
            if spec.get("sex") and m["sex"] != spec["sex"]:
                continue
            if m["birth_day"] is None:
                due.append({"programme": prog,
                            "status": "age-unknown — stated, not assumed"})
                continue
            age = day - m["birth_day"]
            if age < spec["min_age_days"]:
                continue
            if spec["max_age_days"] is not None and \
                    age > spec["max_age_days"]:
                continue
            due.append({"programme": prog, "status": "due"})
        return due

    # --- KPIs -------------------------------------------------------------
    def kpis(self, day):
        overdue = self.overdue_recalls(day)
        chronic = sum(len(m["conditions"]) for m in self.members.values())
        reviewed = sum(len(m["recalls"]) for m in self.members.values())
        payload = {
            "clinician": self.clinician, "facility": self.facility,
            "panel_size": len(self.members), "cap": self.cap,
            "fill_ratio": round(len(self.members) / self.cap, 4),
            "chronic_conditions": chronic,
            "chronic_reviewed_at_least_once": reviewed,
            "overdue_recalls": len(overdue),
            "never_reviewed": sum(1 for o in overdue
                                  if o["never_reviewed"]),
            "version": VERSION,
        }
        payload["sha256"] = _sha(payload)
        return payload

    def _member(self, patient_ref):
        m = self.members.get(patient_ref)
        if m is None:
            raise Error(f"{patient_ref} not on this panel")
        return m


class PanelRegistry:
    """The unit view: many panels, one coverage picture. Unassigned
    citizens are stated — absence from a panel is a fact, not a gap
    in the data."""

    def __init__(self, facility):
        self.facility = facility
        self.panels = {}
        self.unassigned = []

    def open_panel(self, clinician, cap=DEFAULT_PANEL_CAP):
        if clinician in self.panels:
            raise Error(f"{clinician} already holds a panel")
        p = Panel(clinician, self.facility, cap)
        self.panels[clinician] = p
        return p

    def declare_unassigned(self, patient_ref, *, day, reason):
        if not reason:
            raise Error("an unassigned citizen carries a stated reason")
        self.unassigned.append({"patient_ref": patient_ref,
                                "day": day, "reason": reason})

    def find_panel(self, patient_ref):
        for clinician, panel in self.panels.items():
            if patient_ref in panel.members:
                return clinician
        return None  # absence is stated by the caller, not hidden here

    def unit_report(self, day):
        per_panel = {c: p.kpis(day) for c, p in
                     sorted(self.panels.items())}
        total = sum(k["panel_size"] for k in per_panel.values())
        payload = {
            "facility": self.facility, "panels": per_panel,
            "citizens_on_panels": total,
            "declared_unassigned": len(self.unassigned),
            "overdue_recalls": sum(k["overdue_recalls"]
                                   for k in per_panel.values()),
            "version": VERSION,
        }
        payload["sha256"] = _sha(payload)
        return payload
