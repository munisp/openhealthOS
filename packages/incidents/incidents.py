"""incidents.py — patient-safety incident register.

Falls, medication errors, needlesticks, pressure injuries, near-misses.
A hospital that cannot count its harms cannot reduce them — and an
incident register that lets the reporter close their own report is a
compliance prop, not a safety system.

Doctrine:
- INCIDENT TYPES ARE REGISTRY ENTRIES: new types are proposed and
  ratified four-eyes; a type nobody registered refuses.
- THE HAND THAT REPORTS NEVER CLOSES: closure requires a different,
  named hand — stated at close time, refused otherwise.
- NO CLOSURE WITHOUT CAUSE AND ACTION: a close carries contributing
  factors and corrective actions; "resolved" with no reason refuses.
- SEVERITY MOVES UP, STATED: regrading upward is allowed with a
  reason; silently downgrading a harm is refused outright.
"""

import hashlib
import json

INCIDENTS_VERSION = "incidents@1.0.0"


class Error(Exception):
    pass


SEVERITIES = ("no-harm", "mild", "moderate", "severe", "death")

REFERENCE_TYPES = {
    "fall": {"severity_default": "moderate"},
    "medication-error": {"severity_default": "moderate"},
    "needlestick": {"severity_default": "mild"},
    "pressure-injury": {"severity_default": "moderate"},
    "near-miss": {"severity_default": "no-harm"},
    "equipment": {"severity_default": "mild"},
}

_STATES = ("reported", "triaged", "under-investigation", "closed")


class IncidentTypeRegistry:
    """Four-eyes registry of incident types — a type edited in a
    console is a breach, not a config change."""

    def __init__(self):
        self.types = {k: {**v, "status": "ratified"}
                      for k, v in REFERENCE_TYPES.items()}
        self.audit = []

    def propose(self, type_id, definition, *, by, day):
        if type_id in self.types:
            raise Error(f"incident type {type_id} exists; register a "
                        "new id, never edit in place")
        if "severity_default" not in definition:
            raise Error("an incident type declares a default severity")
        if definition["severity_default"] not in SEVERITIES:
            raise Error(f"unknown severity "
                        f"{definition['severity_default']!r}")
        self.types[type_id] = {**definition, "status": "proposed",
                               "proposed_by": by}
        self.audit.append({"op": "propose", "type": type_id,
                           "by": by, "day": day})

    def ratify(self, type_id, *, by, day):
        t = self.types.get(type_id)
        if not t or t["status"] != "proposed":
            raise Error(f"no proposed type {type_id}")
        if by == t["proposed_by"]:
            raise Error("the hand that proposes never ratifies")
        t["status"] = "ratified"
        t["ratified_by"] = by
        self.audit.append({"op": "ratify", "type": type_id,
                           "by": by, "day": day})

    def live(self, type_id):
        t = self.types.get(type_id)
        if not t or t["status"] != "ratified":
            raise Error(f"incident type {type_id} is not ratified — "
                        "an unregistered type of harm cannot be "
                        "reported against")
        return t


class IncidentRegister:
    def __init__(self, registry=None):
        self.registry = registry or IncidentTypeRegistry()
        self.incidents = {}
        self._seq = 0
        self.audit = []

    def _log(self, op, **kw):
        entry = {"op": op, **kw}
        entry["sha256"] = hashlib.sha256(json.dumps(
            {k: v for k, v in entry.items() if k != "sha256"},
            sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.audit.append(entry)

    def report(self, type_id, *, severity=None, subject_ref, location,
               narrative, by, day):
        t = self.registry.live(type_id)      # refuses dead types
        if not narrative or not narrative.strip():
            raise Error("an incident without a narrative is gossip — "
                        "state what happened")
        sev = severity or t["severity_default"]
        if sev not in SEVERITIES:
            raise Error(f"unknown severity {sev!r}")
        self._seq += 1
        iid = f"INC-{self._seq:05d}"
        self.incidents[iid] = {
            "id": iid, "type": type_id, "severity": sev,
            "subject_ref": subject_ref, "location": location,
            "narrative": narrative, "state": "reported",
            "reported_by": by, "reported_day": day,
            "factors": [], "actions": [], "history": []}
        self._log("report", incident=iid, type=type_id,
                  severity=sev, by=by, day=day)
        return iid

    def _inc(self, iid):
        inc = self.incidents.get(iid)
        if not inc:
            raise Error(f"unknown incident {iid}")
        return inc

    def _move(self, inc, state, *, by, day, note=None):
        inc["history"].append({"from": inc["state"], "to": state,
                               "by": by, "day": day, "note": note})
        inc["state"] = state

    def regrade(self, iid, severity, *, reason, by, day):
        """Severity moves UP, stated. Downgrade refuses — a harm is
        never quietly minimised."""
        inc = self._inc(iid)
        if severity not in SEVERITIES:
            raise Error(f"unknown severity {severity!r}")
        if SEVERITIES.index(severity) <= SEVERITIES.index(
                inc["severity"]):
            raise Error("severity moves up only, and with a stated "
                        "reason — a harm is never quietly downgraded")
        if not reason:
            raise Error("regrade carries a stated reason")
        old = inc["severity"]
        inc["severity"] = severity
        self._log("regrade", incident=iid, **{"from": old},
                  to=severity, reason=reason, by=by, day=day)
        return inc

    def triage(self, iid, *, investigator, by, day):
        inc = self._inc(iid)
        if inc["state"] != "reported":
            raise Error(f"incident {iid} is {inc['state']}, not "
                        "reported — triage happens once")
        inc["investigator"] = investigator
        self._move(inc, "triaged", by=by, day=day)
        self._log("triage", incident=iid, investigator=investigator,
                  by=by, day=day)
        return inc

    def root_cause(self, iid, *, factors, by, day):
        inc = self._inc(iid)
        if inc["state"] not in ("triaged", "under-investigation"):
            raise Error(f"incident {iid} is {inc['state']} — root "
                        "cause follows triage")
        if not factors:
            raise Error("root cause with no contributing factors is "
                        "a shrug — state at least one")
        inc["factors"].extend(factors)
        if inc["state"] == "triaged":
            self._move(inc, "under-investigation", by=by, day=day)
        self._log("root-cause", incident=iid, factors=len(factors),
                  by=by, day=day)
        return inc

    def close(self, iid, *, actions, by, day):
        """Closure needs corrective actions AND a different hand than
        the reporter."""
        inc = self._inc(iid)
        if inc["state"] != "under-investigation":
            raise Error(f"incident {iid} is {inc['state']} — only an "
                        "investigated incident closes")
        if by == inc["reported_by"]:
            raise Error("the hand that reports never closes — a "
                        "second named hand closes, stated")
        if not actions:
            raise Error("closure without a corrective action teaches "
                        "nothing — refused")
        inc["actions"].extend(actions)
        self._move(inc, "closed", by=by, day=day)
        self._log("close", incident=iid, actions=len(actions),
                  by=by, day=day)
        return inc

    def report_stats(self, *, from_day=None, to_day=None):
        """The safety dashboard: counts by type and severity, open
        incidents stated, never smoothed."""
        out = {"by_type": {}, "by_severity": {}, "open": 0,
               "closed": 0}
        for inc in self.incidents.values():
            if from_day is not None and inc["reported_day"] < from_day:
                continue
            if to_day is not None and inc["reported_day"] > to_day:
                continue
            out["by_type"][inc["type"]] = \
                out["by_type"].get(inc["type"], 0) + 1
            out["by_severity"][inc["severity"]] = \
                out["by_severity"].get(inc["severity"], 0) + 1
            if inc["state"] == "closed":
                out["closed"] += 1
            else:
                out["open"] += 1
        return out
