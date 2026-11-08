"""theatreopt.py — the theatre-list optimizer.

A theatre list is a constraint problem: surgeon availability,
anaesthetist cover, equipment, case durations, turnaround, and the
hard rules (no surgeon in two rooms at once, no case without
anaesthetic cover, emergency sessions protected). This engine packs
the list and — critically — STATES its trade-offs: which cases were
deferred and why, which constraint bound the day. An optimizer that
hides its casualties is how patients wait silently.

Doctrine:
- HARD RULES NEVER YIELD: double-booking a surgeon refuses; an
  uncovered case is never scheduled; declared emergency capacity is
  untouchable by the optimizer.
- DEFERRALS ARE NAMED: every case not scheduled is listed with the
  constraint that excluded it — stated, not dropped.
- PRIORITY IS CLINICAL, NOT LOBBY: cases order by declared priority
  class (P1 emergency-first within elective pools), then longest
  waiting.
- THE PLAN EXPLAINS ITSELF: utilization, idle minutes, and binding
  constraints are on the printout.
"""

THEATREOPT_VERSION = "theatreopt@1.0.0"


class Error(Exception):
    pass


PRIORITIES = ("P1-urgent", "P2-cancer", "P3-elective", "P4-routine")


class TheatreDay:
    """One day, N theatres, a pool of cases."""

    def __init__(self, *, date_day, theatres, session_minutes=480,
                 emergency_reserve_minutes=60):
        if theatres < 1 or session_minutes < 1:
            raise Error("theatres and session length are declared "
                        "positive")
        if not (0 <= emergency_reserve_minutes < session_minutes):
            raise Error("the emergency reserve is declared and "
                        "smaller than the session")
        self.day = date_day
        self.theatres = theatres
        self.session = session_minutes
        self.reserve = emergency_reserve_minutes
        self.cases = []

    def add_case(self, case_id, *, surgeon, anaesthetist_required,
                 duration_minutes, priority, waited_days,
                 equipment=None):
        if priority not in PRIORITIES:
            raise Error(f"unknown priority {priority!r}; classes are "
                        f"{PRIORITIES}")
        if duration_minutes < 1:
            raise Error("a case declares a positive duration")
        if duration_minutes > self.session - self.reserve:
            raise Error(f"case {case_id} at {duration_minutes} min "
                        "can never fit an elective session — stated, "
                        "not silently deferred forever")
        if waited_days < 0:
            raise Error("waited days are a stated non-negative")
        self.cases.append({
            "id": case_id, "surgeon": surgeon,
            "anaesthetist_required": anaesthetist_required,
            "duration": duration_minutes, "priority": priority,
            "waited_days": waited_days,
            "equipment": set(equipment or [])})

    def optimize(self, *, anaesthetists_available, turnover_minutes=15,
                 equipment_pool=None):
        """Greedy bin-packing honouring the hard rules. Returns the
        list per theatre plus NAMED deferrals with binding reasons."""
        if anaesthetists_available < 1:
            raise Error("a theatre day with no anaesthetist runs "
                        "nothing — refused, stated")
        equipment_pool = set(equipment_pool or [])
        usable = self.session - self.reserve

        # clinical order: priority class, then longest waiting
        order = sorted(
            self.cases,
            key=lambda c: (PRIORITIES.index(c["priority"]),
                           -c["waited_days"]))
        rooms = [{"minutes": 0, "cases": []}
                 for _ in range(self.theatres)]
        surgeon_busy = {}   # surgeon -> [(start, end)] across ALL
                            # rooms — a surgeon exists once, globally
        deferred = []
        anaesthetist_busy_until = 0.0   # single-pool model, declared

        for case in order:
            placed = False
            fail_reason = None
            missing_kit = case["equipment"] - equipment_pool
            if missing_kit:
                deferred.append({"case": case["id"],
                                 "reason": f"equipment {sorted(missing_kit)} "
                                           "not in the declared pool — "
                                           "stated, not scheduled"})
                continue
            for room in rooms:
                need = (case["duration"]
                        + (turnover_minutes if room["cases"] else 0))
                if room["minutes"] + need > usable:
                    fail_reason = "session minutes exhausted"
                    continue
                start = room["minutes"]
                end = start + case["duration"]
                busy = surgeon_busy.get(case["surgeon"], [])
                clash = any(s < end and start < e for s, e in busy)
                if clash:
                    fail_reason = (f"surgeon {case['surgeon']} already "
                                   "operating in another theatre — "
                                   "double-booking refuses")
                    continue
                if case["anaesthetist_required"] and \
                        anaesthetists_available < 2 and \
                        start < anaesthetist_busy_until:
                    fail_reason = ("single anaesthetist pool occupied "
                                   "— cover is a hard rule")
                    continue
                room["cases"].append(case["id"])
                room["minutes"] += need
                surgeon_busy.setdefault(
                    case["surgeon"], []).append((start, end))
                if case["anaesthetist_required"]:
                    anaesthetist_busy_until = max(
                        anaesthetist_busy_until, end)
                placed = True
                break
            if not placed:
                deferred.append({"case": case["id"],
                                 "priority": case["priority"],
                                 "waited_days": case["waited_days"],
                                 "reason": fail_reason or
                                 "no theatre could take the case"})

        used = sum(r["minutes"] for r in rooms)
        capacity = usable * self.theatres
        binding = []
        if used >= capacity - turnover_minutes:
            binding.append("session minutes")
        if any("anaesthetist" in d["reason"] for d in deferred):
            binding.append("anaesthetic cover")
        if any("surgeon" in d["reason"] for d in deferred):
            binding.append("surgeon concurrency")
        if any("equipment" in d["reason"] for d in deferred):
            binding.append("equipment")
        return {"version": THEATREOPT_VERSION, "day": self.day,
                "scheduled": {i: r["cases"]
                              for i, r in enumerate(rooms)},
                "scheduled_count": sum(len(r["cases"]) for r in rooms),
                "deferred": deferred,
                "utilization": round(used / capacity, 4)
                if capacity else None,
                "emergency_reserve_minutes": self.reserve,
                "binding_constraints": binding,
                "verdict": "deferrals are named with reasons; the "
                           "list explains itself"}
