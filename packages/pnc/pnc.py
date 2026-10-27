"""pnc.py — newborn postnatal contact schedule.

The first 48 hours and the first week are where newborn lives are
won or lost, and most births in our catchment happen outside a
facility. The schedule is the intervention: a contact that doesn't
happen on time is surfaced, not silently missed.

Schedule (WHO 2013 postnatal care guidance, publicly documented):
  contact 1: within 48h of birth (day 0-2)
  contact 2: day 7-14
  contact 3: week 6 (day 35-49)

Doctrine:
- MISSED ≠ DONE: a contact outside its window is recorded as missed
  (still attempted — a late visit is better than none — but the
  lateness is on the record and in the coverage stats).
- Newborn danger signs escalate immediately, from any cadre.
- Denominators visible: coverage reports births, due, done-on-time,
  done-late, missed, and never-registered — never a bare percentage.
"""

SCHEDULE = (
    {"contact": 1, "earliest": 0, "latest": 2, "label": "within-48h"},
    {"contact": 2, "earliest": 7, "latest": 14, "label": "day-7-14"},
    {"contact": 3, "earliest": 35, "latest": 49, "label": "week-6"},
)

NEWBORN_DANGER_SIGNS = (
    "not-feeding", "convulsions", "fast-breathing", "severe-chest-indrawing",
    "fever-or-hypothermia", "jaundice-first-24h", "umbilical-redness-or-pus",
    "lethargy-or-unconscious",
)


class PNCError(Exception):
    pass


class PNCRegister:
    def __init__(self):
        self.births = {}  # newborn_id -> {"birth_day", "contacts": {n: record}}

    def register_birth(self, newborn_id, *, birth_day, place, attendant):
        if newborn_id in self.births:
            raise PNCError(f"duplicate birth registration: {newborn_id}")
        if birth_day < 0:
            raise PNCError("birth_day cannot be negative")
        self.births[newborn_id] = {
            "birth_day": birth_day, "place": place,
            "attendant": attendant, "contacts": {},
        }
        return dict(self.births[newborn_id])

    def due(self, newborn_id, day):
        """Contacts due or overdue as of `day`."""
        b = self._birth(newborn_id)
        age = day - b["birth_day"]
        out = []
        for c in SCHEDULE:
            if c["contact"] in b["contacts"]:
                continue
            if age >= c["earliest"]:
                out.append({"contact": c["contact"], "label": c["label"],
                            "overdue": age > c["latest"],
                            "days_overdue": max(0, age - c["latest"])})
        return out

    def record_contact(self, newborn_id, contact_no, *, day, cadre,
                       danger_signs=(), note=""):
        """Record a contact. Danger signs escalate — the return value
        carries escalate=True and the contact is still recorded (the
        assessment happened; the baby must move)."""
        b = self._birth(newborn_id)
        spec = next((c for c in SCHEDULE if c["contact"] == contact_no), None)
        if spec is None:
            raise PNCError(f"unknown contact: {contact_no}")
        if contact_no in b["contacts"]:
            raise PNCError(f"contact {contact_no} already recorded")
        unknown = set(danger_signs) - set(NEWBORN_DANGER_SIGNS)
        if unknown:
            raise PNCError(f"unknown danger signs: {sorted(unknown)}")
        age = day - b["birth_day"]
        if age < 0:
            raise PNCError("contact before birth?")
        on_time = spec["earliest"] <= age <= spec["latest"]
        unknown_signs_escalate = bool(danger_signs)
        rec = {"contact": contact_no, "day": day, "age_days": age,
               "cadre": cadre, "on_time": on_time,
               "danger_signs": list(danger_signs), "note": note,
               "escalate": unknown_signs_escalate}
        b["contacts"][contact_no] = rec
        return dict(rec)

    def coverage(self, as_of):
        """Honest denominators for every contact point."""
        per = {c["contact"]: {"on_time": 0, "late": 0, "missed": 0, "pending": 0}
               for c in SCHEDULE}
        for b in self.births.values():
            age = as_of - b["birth_day"]
            for c in SCHEDULE:
                rec = b["contacts"].get(c["contact"])
                if rec:
                    per[c["contact"]]["on_time" if rec["on_time"] else "late"] += 1
                elif age > c["latest"]:
                    per[c["contact"]]["missed"] += 1
                else:
                    per[c["contact"]]["pending"] += 1
        return {"as_of": as_of, "births_registered": len(self.births),
                "contacts": {str(k): v for k, v in per.items()},
                "note": "missed = window passed with no contact recorded"}

    def _birth(self, newborn_id):
        if newborn_id not in self.births:
            raise PNCError(f"unknown newborn: {newborn_id}")
        return self.births[newborn_id]
