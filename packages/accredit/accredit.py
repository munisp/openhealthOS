"""Facility licensing & accreditation (ADR-0058): inspection checklists,
weighted scoring, renewal workflow. Makes the platform the regulator's tool.
"""
from __future__ import annotations
import json, sys

CHECKLIST = [
    {"id": "staffing", "label": "Qualified staffing per cadre norms", "weight": 25},
    {"id": "infection-control", "label": "IPC protocols and supplies", "weight": 20},
    {"id": "coldchain", "label": "Cold-chain equipment and logs", "weight": 15},
    {"id": "records", "label": "Records completeness (HMIS reporting)", "weight": 15},
    {"id": "medicines", "label": "Essential medicines availability", "weight": 15},
    {"id": "infrastructure", "label": "Water, power, waste management", "weight": 10},
]
GRADE_BANDS = [(90, "A"), (75, "B"), (60, "C"), (0, "D")]

def score_inspection(scores: dict) -> dict:
    """scores: {checklist_id: 0-100}"""
    total = 0.0
    missing = []
    for item in CHECKLIST:
        v = scores.get(item["id"])
        if v is None:
            missing.append(item["id"]); continue
        total += item["weight"] * max(0.0, min(v, 100.0)) / 100
    grade = next(g for thresh, g in GRADE_BANDS if total >= thresh)
    return {"score": round(total, 1), "grade": grade, "missing_sections": missing,
            "complete": not missing,
            "licence_action": ("renew" if grade in ("A", "B")
                               else "conditional" if grade == "C" else "suspend")}

def renewal_due(licences: list[dict], as_of: str, within_days=60) -> list[dict]:
    from datetime import date
    today = date.fromisoformat(as_of)
    out = []
    for l in licences:
        days = (date.fromisoformat(l["expires"]) - today).days
        if days <= within_days:
            out.append({**l, "days_left": days, "expired": days < 0})
    return sorted(out, key=lambda x: x["days_left"])

def main(argv):
    doc = json.load(sys.stdin)
    print(json.dumps(score_inspection(doc.get("scores", {})), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
