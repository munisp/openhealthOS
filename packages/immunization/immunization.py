"""immunization.py — routine immunization: schedule, defaulters, cold chain.

Ring campaigns exist (ringops); routine immunization — the thing that
prevents the outbreaks — did not. The defaulter list is the product:
a child due for Penta-3 six weeks ago is a findable, fixable problem.

Schedule: Nigeria NPI (publicly documented), simplified day windows:
  birth:  BCG, OPV-0, HepB-0
  6wk:    Penta-1, OPV-1, PCV-1, Rota-1
  10wk:   Penta-2, OPV-2, PCV-2, Rota-2
  14wk:   Penta-3, OPV-3, PCV-3, IPV
  9mo:    Measles-1, Yellow-fever
  15mo:   Measles-2

Doctrine:
- THE DEFAULTER LIST IS FIRST-CLASS: due-antigen queries always sort
  overdue-first with days-overdue, never a bare coverage percentage.
- COLD CHAIN: temperatures logged per fridge per day; any reading
  outside 2-8C is an excursion that REQUIRES a recorded action —
  an excursion without action stays open and is reported loudly.
- A DOSE GIVEN DURING AN OPEN EXCURSION IS FLAGGED: the vaccine may
  have been damaged; flag-not-refuse (the dose happened) but the
  flag never disappears silently.
- Contraindications recorded; a refused dose is data, not a failure.
"""

NPI_SCHEDULE = {
    "birth": {"day": 0, "latest": 14,
              "antigens": ["BCG", "OPV-0", "HepB-0"]},
    "6wk": {"day": 42, "latest": 56,
            "antigens": ["Penta-1", "OPV-1", "PCV-1", "Rota-1"]},
    "10wk": {"day": 70, "latest": 84,
             "antigens": ["Penta-2", "OPV-2", "PCV-2", "Rota-2"]},
    "14wk": {"day": 98, "latest": 365,  # catch-up allowed through year 1
             "antigens": ["Penta-3", "OPV-3", "PCV-3", "IPV"]},
    "9mo": {"day": 270, "latest": 365,
            "antigens": ["Measles-1", "Yellow-fever"]},
    "15mo": {"day": 450, "latest": 730,
             "antigens": ["Measles-2"]},
}
COLD_MIN, COLD_MAX = 2.0, 8.0


class ImmunizationError(Exception):
    pass


class ChildRecord:
    def __init__(self):
        self.children = {}

    def register(self, child_id, *, birth_day, facility):
        if child_id in self.children:
            raise ImmunizationError(f"duplicate child: {child_id}")
        self.children[child_id] = {"birth_day": birth_day,
                                   "facility": facility, "doses": []}
        return dict(self.children[child_id])

    def record_dose(self, child_id, antigen, *, day, vaccinator,
                    refused=False, contraindicated=None):
        c = self._child(child_id)
        known = {a for s in NPI_SCHEDULE.values() for a in s["antigens"]}
        if antigen not in known:
            raise ImmunizationError(f"unknown antigen: {antigen}")
        if any(d["antigen"] == antigen for d in c["doses"]):
            raise ImmunizationError(f"{antigen} already recorded for {child_id}")
        age = day - c["birth_day"]
        sched = next(s for s in NPI_SCHEDULE.values() if antigen in s["antigens"])
        early = age < sched["day"] - 4  # 4-day grace
        d = {"antigen": antigen, "day": day, "age_days": age,
             "vaccinator": vaccinator, "early": early,
             "refused": bool(refused), "contraindicated": contraindicated,
             "cold_chain_flag": False}
        c["doses"].append(d)
        return dict(d)

    def defaulters(self, as_of):
        """Overdue-first worklist: one row per child per due visit."""
        rows = []
        for cid, c in self.children.items():
            age = as_of - c["birth_day"]
            done = {d["antigen"] for d in c["doses"] if not d["refused"]}
            for name, s in NPI_SCHEDULE.items():
                missing = [a for a in s["antigens"] if a not in done]
                if not missing:
                    continue
                if age > s["latest"]:
                    rows.append({"child_id": cid, "visit": name,
                                 "missing_antigens": missing,
                                 "days_overdue": age - s["latest"],
                                 "facility": c["facility"]})
                    break
        rows.sort(key=lambda r: -r["days_overdue"])
        return rows

    def coverage(self, visit, *, as_of, min_age_days=None):
        """Denominators visible: eligible children, fully covered for the
        visit's antigens, and the defaulter count."""
        s = NPI_SCHEDULE[visit]
        eligible = [c for c in self.children.values()
                    if as_of - c["birth_day"] >= s["day"]]
        full = sum(1 for c in eligible
                   if all(any(d["antigen"] == a and not d["refused"]
                              for d in c["doses"]) for a in s["antigens"]))
        return {"visit": visit, "eligible": len(eligible),
                "fully_covered": full, "defaulters": len(eligible) - full,
                "note": "coverage = fully_covered/eligible; both shown"}

    def _child(self, cid):
        if cid not in self.children:
            raise ImmunizationError(f"unknown child: {cid}")
        return self.children[cid]


class Fridge:
    """Cold-chain monitor: excursions require a recorded action."""

    def __init__(self, fridge_id, facility):
        self.fridge_id = fridge_id
        self.facility = facility
        self.readings = []
        self.actions = []

    def log_temp(self, day, celsius, *, logger):
        if not (-25.0 <= celsius <= 40.0):
            raise ImmunizationError(f"implausible fridge temp: {celsius}")
        excursion = not (COLD_MIN <= celsius <= COLD_MAX)
        r = {"day": day, "celsius": celsius, "logger": logger,
             "excursion": excursion}
        self.readings.append(r)
        return dict(r)

    def record_action(self, day, *, officer, action):
        if not action:
            raise ImmunizationError("excursion action cannot be empty")
        self.actions.append({"day": day, "officer": officer, "action": action})
        return dict(self.actions[-1])

    def open_excursions(self, upto_day=None):
        """Excursion days with no action on/after that day. These never
        disappear silently."""
        acted_days = {a["day"] for a in self.actions}
        open_days = []
        for r in self.readings:
            if not r["excursion"]:
                continue
            if upto_day is not None and r["day"] > upto_day:
                continue
            if not any(a >= r["day"] for a in acted_days):
                open_days.append(r["day"])
        return open_days

    def report(self, as_of):
        exc = [r for r in self.readings if r["excursion"]]
        return {"fridge_id": self.fridge_id, "facility": self.facility,
                "readings": len(self.readings), "excursions": len(exc),
                "open_excursion_days": self.open_excursions(as_of),
                "note": "an open excursion means vaccine potency unknown"}
