"""ANC / maternal pathway (ADR-0058) — WHO 2016 eight-contact model,
danger-sign triage, defaulter logic (mirrors packages/immig), and partograph
validation. Nigeria bears ~20% of global maternal deaths; this is the
highest-clinical-value specialty module.
"""
from __future__ import annotations
from datetime import date, timedelta
import json, sys

# WHO 2016 ANC contact schedule: gestational week targets
ANC_CONTACTS = [12, 20, 26, 30, 34, 36, 38, 40]
GA_WINDOW_DAYS = 14  # a contact is overdue 14 days past its target week

DANGER_SIGNS = {
    "vaginal_bleeding": ("critical", "Possible APH — refer immediately, do not delay"),
    "severe_headache_blurred_vision": ("critical", "Pre-eclampsia risk — check BP, urine protein, refer if ≥140/90"),
    "convulsions": ("critical", "Eclampsia — MgSO4 protocol, emergency referral"),
    "severe_abdominal_pain": ("critical", "Rule out abruption/rupture — emergency referral"),
    "reduced_fetal_movement": ("major", "Fetal compromise risk — fetal heart check, refer if absent"),
    "fever": ("major", "Rule out malaria/sepsis — RDT, treat per protocol"),
    "swollen_face_hands": ("major", "Pre-eclampsia screen — check BP"),
    "leaking_fluid": ("major", "PROM risk — assess, refer if preterm"),
}

def _d(s): return date.fromisoformat(s)

def gestational_week(lmp: str, on: str | None = None) -> int:
    on_d = _d(on) if on else date.today()
    return (_d(lmp) and (on_d - _d(lmp)).days // 7)

def edd(lmp: str) -> str:
    """Naegele's rule."""
    return (_d(lmp) + timedelta(days=280)).isoformat()

def contact_status(lmp: str, contacts_done: list[int], as_of: str | None = None) -> dict:
    """contacts_done = list of gestational weeks when contacts happened."""
    today = _d(as_of) if as_of else date.today()
    ga_weeks = (today - _d(lmp)).days // 7
    lines, n_due, n_overdue = [], 0, 0
    for i, w in enumerate(ANC_CONTACTS, 1):
        done = any(abs(c - w) <= 2 for c in contacts_done)
        if done:
            st = "DONE"
        elif ga_weeks > w + GA_WINDOW_DAYS // 7 + 1:
            st = "OVERDUE"; n_overdue += 1
        elif ga_weeks >= w - 1:
            st = "DUE"; n_due += 1
        else:
            st = "UPCOMING"
        lines.append({"contact": i, "week": w, "status": st})
    return {"ga_weeks": ga_weeks, "edd": edd(lmp), "contacts": lines,
            "due": n_due, "overdue": n_overdue,
            "defaulter": n_overdue > 0, "post_term": ga_weeks > 42}

def triage_danger(signs: list[str]) -> list[dict]:
    out = []
    for s in signs:
        sev, msg = DANGER_SIGNS.get(s, ("info", f"Unmapped sign '{s}' — clinical review"))
        out.append({"sign": s, "severity": sev, "action": msg})
    return sorted(out, key=lambda x: 0 if x["severity"] == "critical" else 1)

# Partograph: alert line = 1cm/hr from 4cm. observations = [(hour, dilation_cm)]
def partograph_alert(observations: list[tuple[float, float]]) -> dict:
    pts = sorted(observations)
    if len(pts) < 2:
        return {"alert": False, "reason": "insufficient observations"}
    (h0, d0), (h1, d1) = pts[-2], pts[-1]
    rate = (d1 - d0) / max(h1 - h0, 0.25)
    alert = d0 >= 4 and rate < 1.0
    return {"alert": alert, "rate_cm_hr": round(rate, 2),
            "action": "Crossed alert line — reassess, consider referral" if alert else "Progress normal"}

def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    doc = json.load(sys.stdin)
    if cmd == "status":
        print(json.dumps(contact_status(doc["lmp"], doc.get("contacts", []), doc.get("as_of"))))
        return 0
    if cmd == "triage":
        print(json.dumps({"findings": triage_danger(doc.get("signs", []))}))
        return 0
    if cmd == "partograph":
        print(json.dumps(partograph_alert([tuple(x) for x in doc.get("observations", [])])))
        return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
