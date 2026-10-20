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

def main(argv):
    doc = json.load(sys.stdin)
    out = {**aware_report(doc.get("prescriptions", [])),
           "switches": iv_oral_switches(doc.get("prescriptions", []))}
    print(json.dumps(out, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
