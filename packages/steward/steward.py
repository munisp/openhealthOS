"""Antimicrobial stewardship + pharmacovigilance (ADR-0058): prescribing
pattern analytics (AWaRe classification), IV-to-oral switch flags, and
adverse-event report bundles in NAFDAC-style format.
"""
from __future__ import annotations
import json, sys
from collections import Counter

# WHO AWaRe (subset): Access preferred; Watch = resistance risk; Reserve = last resort
AWARE = {
    "NG-AMOX500": "Access", "NG-AMOX250S": "Access", "NG-DOXY100": "Access",
    "NG-CIPRO500": "Watch", "NG-ACT-AL": "Access", "NG-SP": "Access",
}
IV_TO_ORAL = {"NG-AMOX500": "NG-AMOX250S"}  # example mapping

def aware_report(prescriptions: list[dict]) -> dict:
    """prescriptions: [{code, days, route}]"""
    counts = Counter(AWARE.get(p["code"], "Unclassified") for p in prescriptions)
    total = max(len(prescriptions), 1)
    access_pct = round(100 * counts.get("Access", 0) / total, 1)
    return {"counts": dict(counts), "access_pct": access_pct,
            "who_target_met": access_pct >= 60,
            "watch_items": [p["code"] for p in prescriptions if AWARE.get(p["code"]) == "Watch"]}

def iv_oral_switches(prescriptions: list[dict]) -> list[dict]:
    out = []
    for p in prescriptions:
        if p.get("route") == "iv" and p.get("days", 0) >= 2 and p["code"] in IV_TO_ORAL:
            out.append({"code": p["code"], "suggest_oral": IV_TO_ORAL[p["code"]],
                        "reason": "IV >48h with oral alternative — review for switch"})
    return out

def adr_report(patient_id: str, drug_code: str, reaction: str, severity: str,
               onset: str, reporter: str, outcome: str = "unknown") -> dict:
    """NAFDAC-style adverse drug reaction report."""
    if severity not in ("mild", "moderate", "severe", "fatal"):
        raise ValueError(f"severity must be mild|moderate|severe|fatal, got {severity}")
    return {"report_type": "ADR", "patient_id": patient_id, "drug_code": drug_code,
            "reaction": reaction, "severity": severity, "onset": onset,
            "reporter": reporter, "outcome": outcome, "format": "nafdac-e-reporting/v1"}


# ---- antibiotic timeout (the 48-72h review) ----
TIMEOUT_DAYS = 2          # 48h — the classic antibiotic timeout
TIMEOUT_OUTCOMES = ("continue", "stop", "de-escalate", "iv-to-oral")
ROUTE_KINDS = ("iv", "oral", "im")


class TimeoutClock:
    """Every antibiotic course earns a scheduled review at 48h; an
    unreviewed course is a loud overdue item, not a quiet continuation.
    Reviews are append-only and re-arm the clock when the course is
    continued — 'continue' is a decision, not a default."""

    def __init__(self, timeout_days: int = TIMEOUT_DAYS):
        if timeout_days < 1:
            raise ValueError("a timeout shorter than a day is noise")
        self.timeout_days = timeout_days
        self.courses = {}

    def start_course(self, course_id: str, *, patient_id: str,
                     drug_code: str, route: str, day, indication: str,
                     prescriber: str) -> dict:
        if course_id in self.courses:
            raise ValueError(f"duplicate course: {course_id}")
        if route not in ROUTE_KINDS:
            raise ValueError(f"unknown route: {route}")
        if not indication or not indication.strip():
            raise ValueError("an antibiotic without a named indication "
                             "is how resistance breeds")
        if not prescriber:
            raise ValueError("unsigned antibiotics do not start")
        c = {"course_id": course_id, "patient_id": patient_id,
             "drug_code": drug_code, "route": route,
             "start_day": day, "indication": indication,
             "prescriber": prescriber,
             "aware": AWARE.get(drug_code, "Unclassified"),
             "reviews": [], "stopped": None}
        self.courses[course_id] = c
        return dict(c)

    def review_timeout(self, course_id: str, *, day, by: str,
                       outcome: str, note: str = "") -> dict:
        c = self.courses.get(course_id)
        if not c:
            raise ValueError(f"unknown course: {course_id}")
        if c["stopped"] is not None:
            raise ValueError("course stopped — a stopped course reviews "
                             "nothing")
        if outcome not in TIMEOUT_OUTCOMES:
            raise ValueError(f"unknown outcome: {outcome}")
        if not by:
            raise ValueError("unsigned reviews are rumours")
        rev = {"day": day, "by": by, "outcome": outcome, "note": note}
        c["reviews"].append(rev)
        if outcome == "stop":
            c["stopped"] = day
        if outcome == "iv-to-oral":
            c["route"] = "oral"
        return dict(rev)

    def timeouts_due(self, as_of) -> list[dict]:
        """Courses at/past their timeout with no review since — most
        overdue first. A 'continued' course re-arms: the next review
        comes due timeout_days after the last decision."""
        due = []
        for c in self.courses.values():
            if c["stopped"] is not None:
                continue
            anchor = (c["reviews"][-1]["day"] if c["reviews"]
                      else c["start_day"])
            next_due = anchor + self.timeout_days
            if next_due <= as_of:
                due.append({"course_id": c["course_id"],
                            "patient_id": c["patient_id"],
                            "drug_code": c["drug_code"],
                            "route": c["route"], "aware": c["aware"],
                            "due_day": next_due,
                            "overdue_days": as_of - next_due,
                            "reviews_so_far": len(c["reviews"]),
                            "prescriber": c["prescriber"]})
        due.sort(key=lambda c: (-c["overdue_days"], c["course_id"]))
        return due

    def timeout_report(self, as_of) -> dict:
        active = [c for c in self.courses.values()
                  if c["stopped"] is None]
        return {"courses": len(self.courses), "active": len(active),
                "stopped": len(self.courses) - len(active),
                "timeout_reviews": sum(len(c["reviews"])
                                       for c in self.courses.values()),
                "overdue_now": len(self.timeouts_due(as_of)),
                "note": "the overdue number is the stewardship "
                        "programme's pulse; 0 is the only good score"}

def main(argv):
    doc = json.load(sys.stdin)
    out = {**aware_report(doc.get("prescriptions", [])),
           "switches": iv_oral_switches(doc.get("prescriptions", []))}
    print(json.dumps(out, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
