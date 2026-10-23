"""carepanel.py — the CHW chronic-care exception panel.

CHWs and families run the long tail of chronic care; the doctor reviews
EXCEPTIONS, not every patient. This panel turns adherence registries
into a ranked worklist: missed visits and overdue refills float to the
top, stable patients stay invisible — attention goes where it is needed.

Doctrine:
- Ranking is transparent: each row carries its reasons, not a score
  whose derivation nobody can explain.
- A patient with no problems does not appear — the panel is not a
  surveillance list of everyone.
- Pseudonymous refs only (lakefeed contract).
"""

MISSED_VISIT_DAYS = 7
REFILL_DUE_DAYS = 3


def panel(patients, today_day):
    """patients: [{ref, last_visit_day, refill_due_day, condition}]
    -> ranked exception rows with stated reasons."""
    rows = []
    for p in patients:
        reasons = []
        if today_day - p["last_visit_day"] > MISSED_VISIT_DAYS:
            reasons.append(f"missed-visit {today_day - p['last_visit_day']}d")
        if p.get("refill_due_day") is not None and p["refill_due_day"] - today_day <= REFILL_DUE_DAYS:
            days = p["refill_due_day"] - today_day
            reasons.append("refill-overdue" if days < 0 else f"refill-due-{days}d")
        if reasons:
            rows.append({"patient_ref": p["ref"], "condition": p.get("condition"),
                         "reasons": reasons})
    # most reasons first, then most-overdue visit first — explainable order
    rows.sort(key=lambda r: (-len(r["reasons"]), r["reasons"][0]))
    return rows
