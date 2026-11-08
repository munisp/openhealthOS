"""turnover.py — bed-turnover workflow on top of inpatient ADT.

The inpatient package knows a discharged bed is "cleaning". It does
not know WHO is cleaning it, HOW LONG that is taking, or WHO says it
is ready. In a real hospital that queue is throughput: every minute a
bed sits dirty is a minute the ED holds an admission.

Doctrine:
- THE WARD STATE MACHINE IS LAW: this workflow drives bed.state, it
  never bypasses it — release moves cleaning -> available through the
  same Ward.set_bed_state rules.
- THE HAND THAT CLEANS NEVER INSPECTS: release requires an inspector
  different from the crew — stated, refused otherwise.
- THE CLOCK IS DECLARED: each ward declares a turnover SLA in minutes;
  an overdue bed is surfaced with its age, not quietly aging.
- ISOLATION CLEANS ARE TERMINAL CLEANS: a bed flagged terminal-clean
  (isolation discharge) cannot be fast-tracked; the inspector signs
  the terminal clean by name.
"""

TURNOVER_VERSION = "bedturnover@1.0.0"


class Error(Exception):
    pass


class TurnoverBoard:
    """One board per facility, bound to a Ward from inpatient."""

    def __init__(self, ward):
        self.ward = ward
        self.sla_minutes = {}     # ward name -> declared SLA
        self.jobs = {}            # bed_id -> turnover job
        self.completed = []       # finished jobs (for KPIs)

    def declare_sla(self, ward_name, *, minutes, by, day):
        if minutes < 1:
            raise Error("a turnover SLA is a declared positive number "
                        "of minutes")
        self.sla_minutes[ward_name] = {"minutes": minutes,
                                       "declared_by": by,
                                       "declared_day": day}

    def open_job(self, bed_id, *, terminal=False, at_minute, by, day):
        """Called when a bed is vacated — the ward must already show
        it as cleaning (the ADT layer did its job first)."""
        bed = self.ward.beds.get(bed_id)
        if bed is None:
            raise Error(f"unknown bed {bed_id}")
        if bed.state != "cleaning":
            raise Error(f"bed {bed_id} is {bed.state}, not cleaning — "
                        "turnover opens on a vacated bed, never on an "
                        "occupied one")
        if bed_id in self.jobs:
            raise Error(f"turnover for {bed_id} is already open")
        if bed.ward not in self.sla_minutes:
            raise Error(f"ward {bed.ward} has no declared turnover "
                        "SLA — declare the clock before the bed")
        job = {"bed_id": bed_id, "ward": bed.ward,
               "terminal": terminal, "opened_at": at_minute,
               "opened_by": by, "opened_day": day,
               "crew": None, "state": "open"}
        self.jobs[bed_id] = job
        return job

    def assign_crew(self, bed_id, *, crew, eta_minutes, by, day):
        job = self._job(bed_id)
        if job["state"] != "open":
            raise Error(f"turnover {bed_id} is {job['state']}")
        if eta_minutes < 0:
            raise Error("an ETA is a stated non-negative number")
        job["crew"] = crew
        job["eta_minutes"] = eta_minutes
        job["state"] = "assigned"
        return job

    def cleaning_done(self, bed_id, *, at_minute, by, day):
        job = self._job(bed_id)
        if job["state"] != "assigned":
            raise Error(f"turnover {bed_id} is {job['state']} — a "
                        "crew is assigned before it reports done")
        if by != job["crew"]:
            raise Error("the assigned crew reports its own done — "
                        "stated, not assumed")
        job["cleaned_at"] = at_minute
        job["state"] = "cleaned"
        return job

    def release(self, bed_id, *, inspector, at_minute, day,
                terminal_signed_by=None):
        """The inspector is never the crew; a terminal clean is signed
        by name."""
        job = self._job(bed_id)
        if job["state"] != "cleaned":
            raise Error(f"turnover {bed_id} is {job['state']} — only "
                        "a cleaned bed releases")
        if inspector == job["crew"]:
            raise Error("the hand that cleans never inspects — "
                        "release needs a second named hand")
        if job["terminal"] and terminal_signed_by != inspector:
            raise Error("a terminal (isolation) clean is signed by "
                        "the inspector by name — stated, not implied")
        self.ward.set_bed_state(bed_id, "available")
        job["released_at"] = at_minute
        job["released_by"] = inspector
        job["state"] = "released"
        job["turnover_minutes"] = at_minute - job["opened_at"]
        self.completed.append(job)
        del self.jobs[bed_id]
        return job

    def _job(self, bed_id):
        job = self.jobs.get(bed_id)
        if not job:
            raise Error(f"no open turnover for {bed_id}")
        return job

    def overdue(self, *, at_minute):
        """Open jobs past their ward SLA — surfaced with ages."""
        out = []
        for job in self.jobs.values():
            sla = self.sla_minutes[job["ward"]]["minutes"]
            age = at_minute - job["opened_at"]
            if age > sla:
                out.append({"bed_id": job["bed_id"],
                            "ward": job["ward"], "state": job["state"],
                            "age_minutes": age, "sla_minutes": sla,
                            "reason": f"bed {job['bed_id']} has been "
                                      f"{job['state']} for {age} min "
                                      f"against a {sla} min SLA"})
        return out

    def kpis(self, *, ward=None):
        """Turnover KPIs from completed jobs; an empty ward is stated,
        not averaged into fiction."""
        jobs = [j for j in self.completed
                if ward is None or j["ward"] == ward]
        if not jobs:
            return {"ward": ward, "completed": 0,
                    "median_minutes": None,
                    "reason": "no completed turnovers in scope — "
                              "stated, not zeroed"}
        times = sorted(j["turnover_minutes"] for j in jobs)
        mid = len(times) // 2
        median = times[mid] if len(times) % 2 else \
            (times[mid - 1] + times[mid]) / 2
        sla = self.sla_minutes.get(jobs[0]["ward"], {}) \
            .get("minutes")
        breached = sum(1 for j in jobs
                       if sla and j["turnover_minutes"] > sla)
        return {"ward": ward, "completed": len(jobs),
                "median_minutes": median,
                "sla_minutes": sla, "sla_breaches": breached}
