"""vhrescue.py — the heavy end of the claims module: medevac
orchestration, repatriation of remains, the uninsured-emergency fund
ledger, and subrogation — the flows where minutes, dignity, and money
all have to be accounted for at once.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.4):
  - FR-CLM-06: medevac orchestration with a dispatch SLA, and
    repatriation of remains with mortuary and consulate references.
  - FR-CLM-07: uninsured-emergency fund ledger — charity care is
    posted, recovered against, and reported, never absorbed silently.
  - FR-CLM-08: subrogation — when a third party is liable, the scheme
    demands, recovers, or writes off with an approver and a reason.

Doctrines:
- The SLA clock starts at the request, and a breach names itself.
- An approval without a named clinician is a rubber stamp — refused.
- A recovery that doesn't post back to the fund is a leak.
- A write-off without an approver and a reason is a disappearance.
"""

import secrets

VHRESCUE_VERSION = "vhc-rescue@1.0.0"

RESCUE_RULES = {
    "vhc-rescue@1.0.0": {
        "dispatch_sla_hours": 2,
        "assets": ("fixed-wing", "rotary", "boat", "ground"),
        "medevac_states": ("requested", "approved", "dispatched",
                           "arrived", "closed"),
    },
}

MEDEVAC_STATES = ("requested", "approved", "dispatched", "arrived",
                  "closed")
SUBROGATION_STATES = ("identified", "demanded", "recovered",
                      "written-off")


class RescueError(Exception):
    pass


def _refuse(msg):
    raise RescueError(msg)


class RescueRegistry:
    """FR-CLM-06: medevac orchestration + repatriation of remains.
    The timeline is append-only; every transition stamps itself."""

    def __init__(self, *, rules_version="vhc-rescue@1.0.0"):
        if rules_version not in RESCUE_RULES:
            _refuse("unknown rescue rules version")
        self.rules = dict(RESCUE_RULES[rules_version])
        self.rules_version = rules_version
        self.missions = {}
        self.audit = []

    def request_medevac(self, case_id, *, from_facility, to_destination,
                        clinical_justification, requested_by, now_h):
        """A medevac without a clinical justification is a taxi ride."""
        if not clinical_justification:
            _refuse("a medevac without clinical justification is a "
                    "taxi ride")
        if not from_facility or not to_destination or not requested_by:
            _refuse("a mission names from, to, and who asked")
        mission_id = "MED-" + secrets.token_hex(4).upper()
        self.missions[mission_id] = {
            "mission_id": mission_id, "case_id": case_id,
            "kind": "medevac", "state": "requested",
            "from_facility": from_facility,
            "to_destination": to_destination,
            "clinical_justification": clinical_justification,
            "requested_by": requested_by, "requested_h": now_h,
            "timeline": [{"state": "requested", "at_h": now_h}],
            "breaches": [], "asset": None, "approved_by": None,
        }
        self.audit.append({"event": "medevac_requested",
                           "mission": mission_id})
        return self.missions[mission_id]

    def approve(self, mission_id, *, by, note, now_h):
        m = self._mission(mission_id)
        if m["state"] != "requested":
            _refuse("approval follows the request, not the landing")
        if not by or not note:
            _refuse("an approval without a named clinician is a rubber "
                    "stamp")
        m["approved_by"] = by
        m["state"] = "approved"
        m["timeline"].append({"state": "approved", "at_h": now_h,
                              "by": by, "note": note})
        self.audit.append({"event": "medevac_approved",
                           "mission": mission_id, "by": by})
        return m

    def dispatch(self, mission_id, *, asset, now_h):
        m = self._mission(mission_id)
        if m["state"] != "approved":
            _refuse("an unapproved dispatch is a joyride with a "
                    "stretcher")
        if asset not in self.rules["assets"]:
            _refuse("unknown asset — the fleet list is config")
        elapsed = now_h - m["requested_h"]
        if elapsed > self.rules["dispatch_sla_hours"]:
            m["breaches"].append(
                {"clock": "dispatch", "elapsed_hours": elapsed,
                 "budget_hours": self.rules["dispatch_sla_hours"]})
        m["asset"] = asset
        m["state"] = "dispatched"
        m["timeline"].append({"state": "dispatched", "at_h": now_h,
                              "asset": asset, "elapsed_hours": elapsed})
        self.audit.append({"event": "medevac_dispatched",
                           "mission": mission_id, "asset": asset})
        return m

    def arrive(self, mission_id, *, now_h):
        m = self._mission(mission_id)
        if m["state"] != "dispatched":
            _refuse("an arrival without a dispatch is a teleport")
        m["state"] = "arrived"
        m["timeline"].append({"state": "arrived", "at_h": now_h})
        return m

    def close(self, mission_id, *, outcome, now_h):
        m = self._mission(mission_id)
        if m["state"] not in ("arrived", "dispatched"):
            _refuse("a mission closes from arrived — an unflown close "
                    "is a cancellation, and cancellations name reasons")
        if not outcome:
            _refuse("a mission without an outcome is a rumour")
        m["state"] = "closed"
        m["timeline"].append({"state": "closed", "at_h": now_h,
                              "outcome": outcome})
        self.audit.append({"event": "medevac_closed",
                           "mission": mission_id, "outcome": outcome})
        return m

    def repatriate_remains(self, case_id, *, mortuary_ref, consulate_ref,
                           day, note):
        """Repatriation of remains: dignity with paperwork — mortuary
        and consulate references are both mandatory."""
        if not mortuary_ref or not consulate_ref:
            _refuse("remains move with a mortuary reference and a "
                    "consulate reference — both, always")
        if not note:
            _refuse("the record states the circumstance, respectfully "
                    "and completely")
        mission_id = "REP-" + secrets.token_hex(4).upper()
        self.missions[mission_id] = {
            "mission_id": mission_id, "case_id": case_id,
            "kind": "repatriation-of-remains", "state": "closed",
            "mortuary_ref": mortuary_ref, "consulate_ref": consulate_ref,
            "note": note, "day": day,
            "timeline": [{"state": "closed", "day": day}],
            "breaches": [],
        }
        self.audit.append({"event": "remains_repatriated",
                           "mission": mission_id, "day": day})
        return self.missions[mission_id]

    def report(self):
        medevacs = [m for m in self.missions.values()
                    if m["kind"] == "medevac"]
        breached = sum(1 for m in medevacs if m["breaches"])
        return {"missions": len(self.missions),
                "medevacs": len(medevacs),
                "dispatch_sla": {"compliant": len(medevacs) - breached,
                                 "total": len(medevacs)},
                "repatriations": sum(1 for m in self.missions.values()
                                     if m["kind"] ==
                                     "repatriation-of-remains")}

    def _mission(self, mission_id):
        m = self.missions.get(mission_id)
        if m is None:
            _refuse("unknown mission")
        return m


class UninsuredFund:
    """FR-CLM-07: the uninsured-emergency fund ledger. Care given to the
    uncovered is posted — amount, provider, reason — and recovered
    against. Append-only; a silent write-off is a leak with a smile."""

    def __init__(self):
        self.entries = []      # append-only posts and recoveries
        self.audit = []

    def post(self, case_ref, *, provider_ref, amount_minor, day, reason):
        if amount_minor is None or amount_minor <= 0:
            _refuse("a fund post carries an amount")
        if not provider_ref or not reason:
            _refuse("charity care names the provider and the reason — "
                    "absorbed silently is not a policy, it is a leak")
        entry = {"entry_id": "UEF-" + secrets.token_hex(4).upper(),
                 "kind": "post", "case_ref": case_ref,
                 "provider_ref": provider_ref,
                 "amount_minor": amount_minor, "day": day,
                 "reason": reason, "recovered_minor": 0}
        self.entries.append(entry)
        self.audit.append({"event": "fund_posted",
                           "entry": entry["entry_id"], "day": day})
        return entry

    def recover(self, entry_id, *, amount_minor, day, via):
        entry = self._entry(entry_id)
        outstanding = entry["amount_minor"] - entry["recovered_minor"]
        if amount_minor is None or amount_minor <= 0:
            _refuse("a recovery carries an amount")
        if amount_minor > outstanding:
            _refuse("recovering more than outstanding is creative "
                    "accounting")
        if not via:
            _refuse("a recovery names its channel")
        entry["recovered_minor"] += amount_minor
        self.audit.append({"event": "fund_recovered", "entry": entry_id,
                           "amount_minor": amount_minor, "day": day,
                           "via": via})
        return {"entry": entry_id, "recovered_minor": amount_minor,
                "outstanding_minor": entry["amount_minor"]
                - entry["recovered_minor"]}

    def report(self):
        posted = sum(e["amount_minor"] for e in self.entries)
        recovered = sum(e["recovered_minor"] for e in self.entries)
        return {"posts": len(self.entries), "posted_minor": posted,
                "recovered_minor": recovered,
                "outstanding_minor": posted - recovered}

    def _entry(self, entry_id):
        entry = next((e for e in self.entries
                      if e["entry_id"] == entry_id), None)
        if entry is None:
            _refuse("unknown fund entry")
        return entry


class SubrogationRegistry:
    """FR-CLM-08: when a third party is liable, the scheme pursues.
    identified → demanded → recovered / written-off. Recovery posts
    back to the fund; a write-off needs an approver and a reason."""

    def __init__(self, *, fund=None):
        self.claims = {}
        self.fund = fund      # recovered money posts back here
        self.audit = []

    def identify(self, case_id, *, third_party, basis, day):
        if not third_party or not basis:
            _refuse("subrogation names the liable party and the legal "
                    "basis — a suspicion is not a claim")
        sub_id = "SUB-" + secrets.token_hex(4).upper()
        self.claims[sub_id] = {
            "sub_id": sub_id, "case_id": case_id,
            "third_party": third_party, "basis": basis,
            "state": "identified", "identified_day": day,
            "demanded_minor": None, "recovered_minor": 0,
        }
        self.audit.append({"event": "subrogation_identified",
                           "sub": sub_id, "day": day})
        return self.claims[sub_id]

    def demand(self, sub_id, *, amount_minor, day):
        sub = self._sub(sub_id)
        if sub["state"] != "identified":
            _refuse("a demand follows identification, not recovery")
        if amount_minor is None or amount_minor <= 0:
            _refuse("a demand carries an amount")
        sub["demanded_minor"] = amount_minor
        sub["state"] = "demanded"
        sub["demanded_day"] = day
        self.audit.append({"event": "subrogation_demanded",
                           "sub": sub_id, "amount_minor": amount_minor,
                           "day": day})
        return sub

    def recover(self, sub_id, *, amount_minor, day, fund_entry=None):
        """Recovery posts back to the fund when one is wired — money
        that arrives without landing anywhere is a leak."""
        sub = self._sub(sub_id)
        if sub["state"] != "demanded":
            _refuse("recovery follows a demand")
        if amount_minor is None or amount_minor <= 0 or \
                amount_minor > sub["demanded_minor"] - \
                sub["recovered_minor"]:
            _refuse("recovery is bounded by the demand")
        sub["recovered_minor"] += amount_minor
        if sub["recovered_minor"] == sub["demanded_minor"]:
            sub["state"] = "recovered"
        posted = None
        if self.fund is not None:
            posted = self.fund.post(
                sub["case_id"], provider_ref=sub["third_party"],
                amount_minor=amount_minor, day=day,
                reason="subrogation recovery %s" % sub_id)
        self.audit.append({"event": "subrogation_recovered",
                           "sub": sub_id, "amount_minor": amount_minor,
                           "day": day,
                           "fund_entry": (posted["entry_id"]
                                          if posted else None)})
        return {"sub": sub_id, "recovered_minor": amount_minor,
                "fund_entry": posted["entry_id"] if posted else None}

    def write_off(self, sub_id, *, by, reason, day):
        sub = self._sub(sub_id)
        if sub["state"] in ("recovered", "written-off"):
            _refuse("a closed subrogation stays closed")
        if not by or not reason:
            _refuse("a write-off without an approver and a reason is a "
                    "disappearance")
        sub["state"] = "written-off"
        sub["write_off"] = {"by": by, "reason": reason, "day": day}
        self.audit.append({"event": "subrogation_written_off",
                           "sub": sub_id, "by": by, "day": day})
        return sub

    def report(self):
        return {"claims": len(self.claims),
                "by_state": {s: sum(1 for c in self.claims.values()
                                    if c["state"] == s)
                             for s in SUBROGATION_STATES},
                "demanded_minor": sum(c["demanded_minor"] or 0
                                      for c in self.claims.values()),
                "recovered_minor": sum(c["recovered_minor"]
                                       for c in self.claims.values())}

    def _sub(self, sub_id):
        sub = self.claims.get(sub_id)
        if sub is None:
            _refuse("unknown subrogation claim")
        return sub
