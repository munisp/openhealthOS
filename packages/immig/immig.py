#!/usr/bin/env python3
"""immig — national immunization conformance + defaulter tracking + AEFI
(ADR-0054).

Clean-room implementation of the conformance *contract* behind the NPHCDA
Nigeria Immunization FHIR IG and the WHO SMART Guidelines Immunization DAK:
what a conformant Immunization resource must carry, what the EPI schedule
expects, what an AEFI report looks like, and what a DHIS2 aggregate export
must total. We implement the checks against the national EPI schedule —
public-health clinical knowledge, not copied code — so the platform aligns
with the national contract without embedding anyone's licensed tooling.

Four capabilities:

  validate_immunization(resource) -> issues
      Conformance check: vaccine code (CVX/local), occurrence, patient ref,
      lot number, status. A vaccination without a lot number can't be
      traced through a cold-chain failure — required, not optional.

  defaulter_status(birth_date, immunizations, as_of) -> report
      Nigerian EPI schedule (birth / 6 / 10 / 14 wk / 9 mo / 15 mo):
      which antigens are due, overdue, done. Defaulter tracking is the
      outreach engine — a child two weeks late for Penta-3 is a CHW
      task, not a statistic.

  build_aefi_bundle(...) -> FHIR Bundle
      Adverse Event Following Immunization report: Immunization ref +
      Observation (reaction) + Patient, as a transaction bundle the SHR
      accepts directly.

  dhis2_aggregate(immunizations, period) -> counts
      Per-antigen tally for the DHIS2 export path (dhis2-bridge) — the
      numbers the state expects must equal what the ward actually gave.

CLI:
  python3 immig.py validate imm.json
  python3 immig.py defaulters --birth-date 2026-06-01 --history shots.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta

# ------------------------------------------------------------- EPI schedule
# Nigerian EPI (NPHCDA): antigen → due age in days. Doses are positional
# (Penta-1/2/3 at 6/10/14 wk). "window_days" is the grace period before a
# dose counts as OVERDUE — outreach triggers on overdue, not on due.
EPI_SCHEDULE: list[dict] = [
    {"antigen": "BCG",        "dose": 1, "due_days": 0,   "window_days": 14},
    {"antigen": "OPV-0",      "dose": 1, "due_days": 0,   "window_days": 14},
    {"antigen": "HepB-birth", "dose": 1, "due_days": 0,   "window_days": 1},
    {"antigen": "Penta-1",    "dose": 1, "due_days": 42,  "window_days": 14},
    {"antigen": "OPV-1",      "dose": 1, "due_days": 42,  "window_days": 14},
    {"antigen": "PCV-1",      "dose": 1, "due_days": 42,  "window_days": 14},
    {"antigen": "Rota-1",     "dose": 1, "due_days": 42,  "window_days": 14},
    {"antigen": "Penta-2",    "dose": 2, "due_days": 70,  "window_days": 14},
    {"antigen": "OPV-2",      "dose": 2, "due_days": 70,  "window_days": 14},
    {"antigen": "PCV-2",      "dose": 2, "due_days": 70,  "window_days": 14},
    {"antigen": "Rota-2",     "dose": 2, "due_days": 70,  "window_days": 14},
    {"antigen": "Penta-3",    "dose": 3, "due_days": 98,  "window_days": 14},
    {"antigen": "OPV-3",      "dose": 3, "due_days": 98,  "window_days": 14},
    {"antigen": "PCV-3",      "dose": 3, "due_days": 98,  "window_days": 14},
    {"antigen": "IPV-1",      "dose": 1, "due_days": 98,  "window_days": 14},
    {"antigen": "Measles-1",  "dose": 1, "due_days": 274, "window_days": 30},
    {"antigen": "YellowFever","dose": 1, "due_days": 274, "window_days": 30},
    {"antigen": "MenA",       "dose": 1, "due_days": 274, "window_days": 30},
    {"antigen": "Measles-2",  "dose": 2, "due_days": 456, "window_days": 30},
    {"antigen": "HPV",        "dose": 1, "due_days": 3285, "window_days": 365},  # girls 9y
]

# Antigen naming the schedule recognises in an Immunization resource:
# vaccineCode text/code is matched case-insensitively against these.
ANTIGEN_ALIASES = {
    "bcg": "BCG", "opv0": "OPV-0", "opv-0": "OPV-0", "hepb birth": "HepB-birth",
    "hepb-birth": "HepB-birth", "hepb0": "HepB-birth",
    "penta1": "Penta-1", "penta-1": "Penta-1", "pentavalent 1": "Penta-1",
    "penta2": "Penta-2", "penta-2": "Penta-2", "pentavalent 2": "Penta-2",
    "penta3": "Penta-3", "penta-3": "Penta-3", "pentavalent 3": "Penta-3",
    "opv1": "OPV-1", "opv-1": "OPV-1", "opv2": "OPV-2", "opv-2": "OPV-2",
    "opv3": "OPV-3", "opv-3": "OPV-3",
    "pcv1": "PCV-1", "pcv-1": "PCV-1", "pcv2": "PCV-2", "pcv-2": "PCV-2",
    "pcv3": "PCV-3", "pcv-3": "PCV-3", "pcv 10": "PCV-1",
    "rota1": "Rota-1", "rota-1": "Rota-1", "rota2": "Rota-2", "rota-2": "Rota-2",
    "ipv": "IPV-1", "ipv-1": "IPV-1", "ipv1": "IPV-1",
    "measles1": "Measles-1", "measles-1": "Measles-1", "measles 1": "Measles-1",
    "measles2": "Measles-2", "measles-2": "Measles-2",
    "yellow fever": "YellowFever", "yellowfever": "YellowFever",
    "mena": "MenA", "men a": "MenA", "hpv": "HPV",
}


def _d(s: str) -> date:
    return datetime.fromisoformat(str(s)[:10]).date()


def identify_antigen(resource: dict) -> str | None:
    """Map a FHIR Immunization's vaccineCode to a schedule antigen."""
    vc = resource.get("vaccineCode", {})
    candidates = [vc.get("text", "")]
    for c in vc.get("coding", []):
        candidates += [c.get("display", ""), c.get("code", "")]
    for cand in candidates:
        key = " ".join(str(cand).lower().split())
        if key in ANTIGEN_ALIASES:
            return ANTIGEN_ALIASES[key]
        # tolerant contains-match: "Pentavalent 1 (10 wk)" still resolves
        for alias, antigen in ANTIGEN_ALIASES.items():
            if alias in key and len(alias) >= 4:
                return antigen
    return None


# ------------------------------------------------------------- conformance

def validate_immunization(resource: dict) -> list[str]:
    """Conformance issues for one Immunization against the national
    contract's shape. Empty list = conformant."""
    issues: list[str] = []
    if resource.get("resourceType") != "Immunization":
        return ["not an Immunization resource"]
    status = resource.get("status", "")
    if status not in ("completed", "entered-in-error", "not-done"):
        issues.append(f"status {status!r} invalid")
    vc = resource.get("vaccineCode", {})
    if not vc.get("coding") and not vc.get("text"):
        issues.append("vaccineCode missing — what was given?")
    elif identify_antigen(resource) is None:
        issues.append(f"vaccineCode not mapped to an EPI antigen: "
                      f"{vc.get('text') or vc.get('coding')}")
    if not resource.get("occurrenceDateTime"):
        issues.append("occurrenceDateTime missing — when was it given?")
    patient = resource.get("patient", {})
    if not patient.get("reference"):
        issues.append("patient.reference missing — who received it?")
    if status == "completed":
        if not resource.get("lotNumber"):
            issues.append("lotNumber missing on completed dose — "
                          "cannot trace cold-chain/batch failures")
        if not resource.get("primarySource", True) is not None:
            pass  # primarySource optional; presence is good practice
        if not resource.get("location", {}).get("reference") and \
           not resource.get("performer"):
            issues.append("no location or performer — where/who administered?")
    return issues


# ------------------------------------------------------------- defaulters

def defaulter_status(birth_date: str, immunizations: list[dict],
                     as_of: str | None = None) -> dict:
    """Due/overdue/done per schedule line. `immunizations` are FHIR
    Immunization dicts (or plain {vaccine, date} rows)."""
    today = _d(as_of) if as_of else date.today()
    dob = _d(birth_date)
    given: dict[str, date] = {}
    for imm in immunizations:
        if imm.get("resourceType") == "Immunization":
            if imm.get("status") != "completed":
                continue
            antigen = identify_antigen(imm)
            when = imm.get("occurrenceDateTime", "")
        else:
            antigen = ANTIGEN_ALIASES.get(str(imm.get("vaccine", "")).lower())
            when = imm.get("date", "")
        if antigen and when:
            given[antigen] = _d(when)

    lines = []
    n_done = n_due = n_overdue = 0
    for row in EPI_SCHEDULE:
        antigen = row["antigen"]
        due = dob + timedelta(days=row["due_days"])
        overdue_after = due + timedelta(days=row["window_days"])
        if antigen in given:
            status = "DONE"
            n_done += 1
        elif today > overdue_after:
            status = "OVERDUE"
            n_overdue += 1
        elif today >= due:
            status = "DUE"
            n_due += 1
        else:
            status = "UPCOMING"
        lines.append({
            "antigen": antigen, "status": status,
            "due": due.isoformat(),
            "given": given[antigen].isoformat() if antigen in given else None,
            "days_late": (today - overdue_after).days if status == "OVERDUE" else 0,
        })
    actionable = [l for l in lines if l["status"] in ("DUE", "OVERDUE")]
    return {
        "birth_date": dob.isoformat(), "as_of": today.isoformat(),
        "done": n_done, "due": n_due, "overdue": n_overdue,
        "complete": n_done == len(EPI_SCHEDULE),
        "defaulter": n_overdue > 0,
        "next_action": min(actionable, key=lambda l: l["due"])["antigen"] if actionable else None,
        "lines": lines,
    }


# ------------------------------------------------------------- AEFI

def build_aefi_bundle(patient_id: str, immunization_ref: str,
                      reaction_code: str, reaction_text: str,
                      severity: str, onset: str, reporter: str,
                      facility: str) -> dict:
    """AEFI report as a FHIR transaction Bundle — POSTable to the SHR as-is."""
    obs_id = f"aefi-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    return {
        "resourceType": "Bundle", "type": "transaction",
        "entry": [
            {
                "fullUrl": f"urn:uuid:{obs_id}",
                "resource": {
                    "resourceType": "Observation", "id": obs_id,
                    "status": "final",
                    "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                              "code": "exam"}]}],
                    "code": {"coding": [{"system": "http://loinc.org", "code": reaction_code}],
                             "text": f"AEFI: {reaction_text}"},
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "effectiveDateTime": onset,
                    "valueString": f"{reaction_text} (severity: {severity})",
                    "component": [
                        {"code": {"text": "severity"}, "valueString": severity},
                        {"code": {"text": "implicated-vaccine"},
                         "valueReference": {"reference": immunization_ref}},
                    ],
                    "performer": [{"reference": f"Practitioner/{reporter}"}],
                },
                "request": {"method": "POST", "url": "Observation"},
            },
            {
                "fullUrl": "urn:uuid:aefi-flag",
                "resource": {
                    "resourceType": "Flag", "id": f"{obs_id}-flag",
                    "status": "active",
                    "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/flag-category",
                                              "code": "clinical"}]}],
                    "code": {"text": f"AEFI reported — {severity}"},
                    "subject": {"reference": f"Patient/{patient_id}"},
                    "period": {"start": onset},
                },
                "request": {"method": "POST", "url": "Flag"},
            },
        ],
        "meta": {"tag": [{"system": "https://openhealthos.example/aefi", "code": facility}]},
    }


# ------------------------------------------------------------- DHIS2 tally

def dhis2_aggregate(immunizations: list[dict], period: str) -> dict:
    """Per-antigen counts for a YYYYMM period — the numbers dhis2-bridge
    ships must reconcile with what wards actually administered."""
    counts: dict[str, int] = {}
    skipped = 0
    for imm in immunizations:
        if imm.get("status") != "completed":
            skipped += 1
            continue
        when = imm.get("occurrenceDateTime", "")
        if not when.startswith(_period_prefix(period)):
            continue
        antigen = identify_antigen(imm) or "UNMAPPED"
        counts[antigen] = counts.get(antigen, 0) + 1
    return {"period": period, "counts": dict(sorted(counts.items())),
            "total": sum(counts.values()), "skipped_not_completed": skipped}


def _period_prefix(period: str) -> str:
    return f"{period[:4]}-{period[4:6]}" if len(period) == 6 else period


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="immig")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("resource", help="Immunization JSON file")
    d = sub.add_parser("defaulters")
    d.add_argument("--birth-date", required=True)
    d.add_argument("--history", default="", help="JSONL of Immunization resources")
    d.add_argument("--as-of", default="")
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        issues = validate_immunization(json.load(open(args.resource)))
        print(json.dumps({"conformant": not issues, "issues": issues}, indent=2))
        return 0 if not issues else 1
    hist: list[dict] = []
    if args.history:
        with open(args.history) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    hist.append(json.loads(line))
    print(json.dumps(defaulter_status(args.birth_date, hist, args.as_of or None), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
