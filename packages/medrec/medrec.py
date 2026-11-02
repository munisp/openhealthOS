"""medrec.py — medication reconciliation at admission, transfer, and
discharge.

The most dangerous moment for a patient's medications is the moment
they MOVE: admission drops the home meds, discharge drops the new
ones, and both errors are silent because nobody owns the list.
Reconciliation makes the list, gives every entry a provenance, and
forces an explicit decision per drug — continue, stop, substitute,
withhold — because an undecided drug is not a reconciled drug.

Doctrine:
  - PROVENANCE OR IT DIDN'T HAPPEN: a home-med entry without a source
    (patient-report, pharmacy-record, gp-letter…) is fiction, and
    refuses
  - THREE LISTS, ALWAYS: home meds, active orders, decisions — the
    discrepancy is the difference between them, computed, not
    asserted
  - every home med earns a decision; 'no decision' is the open
    discrepancy, listed overdue-first — an undecided drug is not a
    reconciled drug
  - a decision against an active order is loud: ordering a drug the
    reconciliation stopped is flagged, not silently honoured
  - rates show denominators: reconciled rate without n is marketing
"""

MEDREC_VERSION = "medrec@1.0.0"
SETTINGS = ("admission", "transfer", "discharge")
SOURCES = ("patient-report", "family-report", "pharmacy-record",
           "gp-letter", "prior-discharge-list", "brought-in-medications")
DECISIONS = ("continue", "stop", "substitute", "withhold")


class MedRecError(Exception):
    pass


class MedRec:
    def __init__(self):
        self.recons = {}       # (patient_id, setting) -> record

    def start(self, patient_id, *, setting, day, by):
        if setting not in SETTINGS:
            raise MedRecError(f"unknown reconciliation setting: "
                              f"{setting}")
        if not by:
            raise MedRecError("unsigned reconciliation is no "
                              "reconciliation")
        key = (patient_id, setting)
        if key in self.recons:
            raise MedRecError(
                f"{setting} reconciliation already open for "
                f"{patient_id} — the second ring of the bell is not a "
                "second case")
        self.recons[key] = {
            "patient_id": patient_id, "setting": setting,
            "started_day": day, "by": by,
            "home_meds": {},       # drug -> {dose, source, day}
            "orders": set(),       # drugs currently ordered
            "decisions": {},       # drug -> {decision, reason, day, by}
            "medrec_version": MEDREC_VERSION}
        return dict(self.recons[key])

    def record_home_med(self, patient_id, drug, *, setting, dose,
                        source, day):
        if source not in SOURCES:
            raise MedRecError(
                f"unknown provenance: {source} — a med list without a "
                "source is fiction; who TOLD us they take this?")
        if not drug or not dose:
            raise MedRecError("a med entry needs a drug and a dose")
        r = self._r(patient_id, setting)
        if drug in r["home_meds"]:
            raise MedRecError(
                f"{drug} already on the home list — a correction comes "
                "from a new source, not an overwrite")
        r["home_meds"][drug] = {"dose": dose, "source": source,
                                "day": day}
        return dict(r["home_meds"][drug])

    def note_order(self, patient_id, drug, *, setting, day):
        """An active inpatient order the reconciliation must reckon
        with."""
        r = self._r(patient_id, setting)
        r["orders"].add(drug)
        return {"orders": sorted(r["orders"])}

    def reconcile(self, patient_id, drug, *, setting, decision, day,
                  by, reason=None, substitute_with=None):
        """One drug, one explicit decision. 'stop'/'withhold' need a
        reason; 'substitute' needs its replacement."""
        if decision not in DECISIONS:
            raise MedRecError(f"unknown decision: {decision}")
        r = self._r(patient_id, setting)
        if drug not in r["home_meds"]:
            raise MedRecError(
                f"{drug} is not on the home list — reconciling a drug "
                "nobody reported is inventing history")
        if drug in r["decisions"]:
            raise MedRecError("decision already recorded — a change of "
                              "mind is a new reconciliation, not an "
                              "edit")
        if decision in ("stop", "withhold") and not reason:
            raise MedRecError(
                f"{decision} without a reason is a silent omission — "
                "the patient will ask why, and 'the computer said so' "
                "is not an answer")
        if decision == "substitute" and not substitute_with:
            raise MedRecError("substitute with WHAT?")
        if not by:
            raise MedRecError("unsigned decision is no decision")
        r["decisions"][drug] = {"decision": decision, "reason": reason,
                                "substitute_with": substitute_with,
                                "day": day, "by": by}
        return dict(r["decisions"][drug])

    # ---- the discrepancies, computed not asserted ----
    def discrepancies(self, patient_id, setting="admission"):
        """Open work: home meds with no decision (omissions), and
        decisions contradicted by active orders (commission) — the
        two ways transitions of care hurt people."""
        r = self._r(patient_id, setting)
        out = []
        for drug in sorted(set(r["home_meds"]) - set(r["decisions"])):
            out.append({"drug": drug, "kind": "undecided",
                        "source": r["home_meds"][drug]["source"]})
        for drug, d in r["decisions"].items():
            if d["decision"] in ("stop", "withhold") \
                    and drug in r["orders"]:
                out.append({"drug": drug, "kind": "ordered-despite-"
                            + d["decision"],
                            "decision_day": d["day"]})
        return out

    def unreconciled(self, as_of, *, days=1):
        """Every reconciliation with undecided meds, past the grace
        window — overdue-first. The undecided drug at discharge is the
        one that gets lost."""
        out = []
        for (pid, setting), r in self.recons.items():
            undecided = set(r["home_meds"]) - set(r["decisions"])
            if undecided and as_of - r["started_day"] >= days:
                out.append({"patient_id": pid, "setting": setting,
                            "undecided": sorted(undecided),
                            "days_open": as_of - r["started_day"]})
        out.sort(key=lambda x: (-x["days_open"], x["patient_id"]))
        return out

    def report(self):
        """Reconciliation completeness with denominators, per
        setting."""
        per = {}
        for (pid, setting), r in self.recons.items():
            s = per.setdefault(setting, {"meds": 0, "decided": 0,
                                         "reconciliations": 0})
            s["reconciliations"] += 1
            s["meds"] += len(r["home_meds"])
            s["decided"] += len(set(r["home_meds"])
                                & set(r["decisions"]))
        for s in per.values():
            s["decision_rate"] = (round(s["decided"] / s["meds"], 3)
                                  if s["meds"] else None)
        return {"per_setting": per,
                "medrec_version": MEDREC_VERSION,
                "note": "the denominator is home meds recorded; an "
                        "undecided drug is not a reconciled drug"}

    def _r(self, pid, setting):
        key = (pid, setting)
        if key not in self.recons:
            raise MedRecError(f"no {setting} reconciliation for {pid}")
        return self.recons[key]
