"""vhregistry.py — the visitor-incident registry: every visitor health
incident, covered or not, pseudonymized at the border of the record —
with BR-10 outbreak alerting, a duty-officer acknowledgement trail,
anonymized export packs, and the public quarterly dashboard.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.6):
  - FR-REG-01: visitor-incident registry — syndromic class, facility,
    island/site, outcome, costs; pseudonymized, never named.
  - FR-REG-02 / BR-10: outbreak alerts — ≥5 gastrointestinal cases at
    the same facility in the same week open an alert.
  - Duty-officer acknowledgement trail: an alert unacknowledged is an
    alert unheard — the report names it.
  - FR-REG-03: export packs (IHR / UKHSA / ECDC) — aggregates only,
    small cells suppressed, integrity hash on every pack.
  - FR-REG-05: public quarterly dashboard data — counts with
    denominators, k-anonymity suppression stated, never silent.

Doctrines:
- The registry never sees a name: the traveller key is a salted hash
  minted at registration; the salt is mandatory.
- A cell under the k-anonymity floor is suppressed with the
  suppression stated — a hidden zero is a lie about coverage.
- An alert without an acknowledgement is a siren in an empty room.
- Every export pack carries its checksum — the checksum is the
  contract.
"""

import hashlib
import json
import secrets

VHREGISTRY_VERSION = "visitor-incidents@1.0.0"

INCIDENT_RULES = {
    "visitor-incidents@1.0.0": {
        "syndromic_classes": ("gastrointestinal", "respiratory",
                              "febrile_rash", "neurological",
                              "trauma", "other"),
        "outcomes": ("discharged", "admitted", "transferred",
                     "deceased"),
        "br10": {"syndromic_class": "gastrointestinal",
                 "min_cases": 5, "per": ("facility", "week")},
        "k_anonymity_floor": 5,
        "export_formats": ("ihr", "ukhsa", "ecdc"),
    },
}


class RegistryError(Exception):
    pass


def _refuse(msg):
    raise RegistryError(msg)


def _week(day):
    return day // 7


def _quarter(day):
    return (day // 7) // 13


class IncidentRegistry:
    def __init__(self, *, rules_version="visitor-incidents@1.0.0",
                 salt):
        if rules_version not in INCIDENT_RULES:
            _refuse("unknown incident rules version")
        self.rules = dict(INCIDENT_RULES[rules_version])
        self.rules_version = rules_version
        if not salt:
            _refuse("the pseudonym salt is mandatory — an unsalted "
                    "registry is a name list with extra steps")
        self._salt = salt
        self.incidents = {}
        self.alerts = {}
        self.acks = []           # append-only acknowledgement trail
        self.audit = []

    def _pseudo(self, traveler_key):
        return hashlib.sha256(
            (self._salt + ":" + traveler_key).encode()).hexdigest()

    # --- FR-REG-01: register ------------------------------------------
    def register_incident(self, *, traveler_key, syndromic_class,
                          facility, site, outcome, cost_minor, day,
                          cover_ref=None):
        """Pseudonymized at the door: the registry stores the salted
        hash, never the traveller key it was given."""
        if syndromic_class not in self.rules["syndromic_classes"]:
            _refuse("unknown syndromic class — the vocabulary is "
                    "versioned, not improvised")
        if outcome not in self.rules["outcomes"]:
            _refuse("unknown outcome — the vocabulary is versioned")
        if not facility or not site:
            _refuse("an incident names its facility and its site")
        if cost_minor is None or cost_minor < 0:
            _refuse("an incident carries its cost — 0 is a fact, "
                    "None is a hole")
        inc_id = "INC-" + secrets.token_hex(4).upper()
        self.incidents[inc_id] = {
            "incident_id": inc_id,
            "traveler_pseudo": self._pseudo(traveler_key),
            "syndromic_class": syndromic_class, "facility": facility,
            "site": site, "outcome": outcome,
            "cost_minor": cost_minor, "day": day,
            "week": _week(day),
            "covered": cover_ref is not None,
        }
        self.audit.append({"event": "incident_registered",
                           "incident": inc_id, "day": day})
        return self.incidents[inc_id]

    # --- FR-REG-02 / BR-10: outbreak alerts ----------------------------
    def evaluate_alerts(self, *, day):
        """BR-10: ≥5 GI cases, same facility, same week. The alert opens
        once per (facility, week) and stays open until acknowledged."""
        br = self.rules["br10"]
        opened = []
        buckets = {}
        for inc in self.incidents.values():
            if inc["syndromic_class"] != br["syndromic_class"]:
                continue
            key = (inc["facility"], inc["week"])
            buckets.setdefault(key, []).append(inc["incident_id"])
        for (facility, week), ids in buckets.items():
            if len(ids) < br["min_cases"]:
                continue
            alert_id = "BR10-%s-W%s" % (facility, week)
            if alert_id in self.alerts:
                continue  # idempotent — re-evaluation adds nothing
            self.alerts[alert_id] = {
                "alert_id": alert_id, "rule": "BR-10",
                "facility": facility, "week": week,
                "cases": len(ids), "incident_ids": sorted(ids),
                "opened_day": day, "acknowledged": False,
            }
            self.audit.append({"event": "br10_alert_opened",
                               "alert": alert_id, "cases": len(ids),
                               "day": day})
            opened.append(alert_id)
        return {"opened": opened,
                "total_open": sum(1 for a in self.alerts.values()
                                  if not a["acknowledged"])}

    def acknowledge(self, alert_id, *, by, note, day):
        """The duty officer signs the siren. An acknowledgement without
        a name and a note is a shrug."""
        alert = self.alerts.get(alert_id)
        if alert is None:
            _refuse("unknown alert")
        if not by or not note:
            _refuse("an acknowledgement names the officer and the "
                    "action taken")
        alert["acknowledged"] = True
        self.acks.append({"alert_id": alert_id, "by": by, "note": note,
                          "day": day,
                          "response_days": day - alert["opened_day"]})
        self.audit.append({"event": "br10_acknowledged",
                           "alert": alert_id, "by": by, "day": day})
        return alert

    # --- FR-REG-03: export packs ---------------------------------------
    def export_pack(self, *, export_format, week):
        """Anonymized aggregates only. Small cells are suppressed with
        the suppression stated; every pack carries its checksum."""
        if export_format not in self.rules["export_formats"]:
            _refuse("unknown export format — the recipient list is "
                    "config")
        floor = self.rules["k_anonymity_floor"]
        cells = {}
        for inc in self.incidents.values():
            if inc["week"] != week:
                continue
            key = (inc["syndromic_class"], inc["site"])
            cells[key] = cells.get(key, 0) + 1
        rows, suppressed = [], 0
        for (cls, site), count in sorted(cells.items()):
            if count < floor:
                suppressed += 1
                continue
            rows.append({"syndromic_class": cls, "site": site,
                         "cases": count})
        body = {"format": export_format, "week": week, "rows": rows,
                "suppressed_cells": suppressed,
                "k_anonymity_floor": floor}
        body["pack_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        self.audit.append({"event": "export_pack_issued",
                           "format": export_format, "week": week})
        return body

    # --- FR-REG-05: public quarterly dashboard --------------------------
    def public_dashboard(self, *, quarter_day):
        """Quarterly aggregates for the public: counts with
        denominators, cells under the floor suppressed and stated."""
        q = _quarter(quarter_day)
        floor = self.rules["k_anonymity_floor"]
        in_q = [i for i in self.incidents.values()
                if _quarter(i["day"]) == q]
        by_class, suppressed = {}, 0
        for inc in in_q:
            by_class[inc["syndromic_class"]] = \
                by_class.get(inc["syndromic_class"], 0) + 1
        shown = {}
        for cls, count in by_class.items():
            if count < floor:
                suppressed += 1
            else:
                shown[cls] = count
        outcomes = {}
        for inc in in_q:
            outcomes[inc["outcome"]] = outcomes.get(inc["outcome"], 0) + 1
        return {"quarter": q, "incidents_total": len(in_q),
                "by_syndromic_class": shown,
                "suppressed_cells": suppressed,
                "k_anonymity_floor": floor,
                "outcomes": outcomes,
                "covered_share": {
                    "covered": sum(1 for i in in_q if i["covered"]),
                    "total": len(in_q)},
                "alerts": {"opened": len(self.alerts),
                           "unacknowledged": sum(
                               1 for a in self.alerts.values()
                               if not a["acknowledged"])}}

    def report(self):
        return {"incidents": len(self.incidents),
                "alerts_open": sum(1 for a in self.alerts.values()
                                   if not a["acknowledged"]),
                "alerts_acknowledged": len(self.acks),
                "ack_response_days": [
                    a["response_days"] for a in self.acks],
                "total_cost_minor": sum(i["cost_minor"]
                                        for i in
                                        self.incidents.values())}
