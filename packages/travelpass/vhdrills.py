"""Drill automation — gates hold fresh evidence or they hold nothing.

A go-live gate whose rollback drill is 200 days old is not rehearsed,
it is folklore. This module runs drills (rollback, canary halt,
offline-cache expiry), stamps each artifact with a checksum, and tells
the gates when evidence has gone stale. It also checks demo staleness:
regenerate the seed payload and compare checksums — a demo board that
no longer matches the engine is a finding.

A halt condition invented mid-incident is an excuse, not a control —
drill scenarios are declared entries, rehearsed on schedule.
"""

import hashlib
import json

DRILLS_VERSION = "vhdrills@1.0.0"

# scenario entries — declared before the incident, never during
DRILL_SCENARIOS = {
    "rollback": {"max_recovery_minutes": 30,
                 "help": "restore last-known-good from the ledger snapshot"},
    "canary_halt": {"max_recovery_minutes": 5,
                    "help": "freeze ramp at current stage, drain, verify"},
    "offline_cache_expiry": {"max_recovery_minutes": 5,
                             "help": "cache older than cache_max_age shows RED"},
    "key_rotation": {"max_recovery_minutes": 60,
                     "help": "rotate signing key, old keys verify-only"},
}

DEFAULT_FRESHNESS_DAYS = 30


class DrillError(Exception):
    pass


def _refuse(msg):
    raise DrillError(msg)


class DrillBook:
    def __init__(self, *, freshness_days=DEFAULT_FRESHNESS_DAYS):
        self.freshness_days = freshness_days
        self.runs = []          # append-only drill artifacts

    def run_drill(self, scenario, *, day, recovered_minutes, evidence,
                  run_by):
        if scenario not in DRILL_SCENARIOS:
            _refuse(f"undeclared drill {scenario!r} — a halt condition "
                    "invented mid-incident is an excuse, not a control")
        if not evidence or not run_by:
            _refuse("a drill without evidence and a name is a story")
        spec = DRILL_SCENARIOS[scenario]
        artifact = {"scenario": scenario, "day": day,
                    "recovered_minutes": recovered_minutes,
                    "within_budget": (recovered_minutes <=
                                      spec["max_recovery_minutes"]),
                    "evidence": evidence, "run_by": run_by}
        canon = json.dumps(artifact, sort_keys=True).encode()
        artifact["sha256"] = hashlib.sha256(canon).hexdigest()
        self.runs.append(artifact)
        return dict(artifact)

    def freshness(self, *, day):
        """Per scenario: days since last passing run. Stale = finding."""
        out = {}
        for name in DRILL_SCENARIOS:
            passing = [r for r in self.runs
                       if r["scenario"] == name and r["within_budget"]]
            if not passing:
                out[name] = {"status": "never_run", "stale": True}
                continue
            last = max(r["day"] for r in passing)
            age = day - last
            out[name] = {"status": "fresh" if age <= self.freshness_days
                         else "stale", "age_days": age,
                         "last_sha256": [r for r in passing
                                         if r["day"] == last][0]["sha256"]}
        return out

    def gate_evidence_ok(self, *, day):
        return all(v["status"] == "fresh" for v in
                   self.freshness(day=day).values())


def demo_staleness(live_payloads, regenerated_payloads):
    """Compare checksum-stamped payloads against a regeneration. Any
    drift is named, never smoothed over."""
    stale = []
    for name, live in live_payloads.items():
        regen = regenerated_payloads.get(name)
        if regen is None:
            stale.append({"payload": name, "reason": "missing in regeneration"})
        elif live.get("sha256") != regen.get("sha256"):
            stale.append({"payload": name,
                          "reason": "checksum drift — the board no longer "
                                    "matches the engine"})
    return stale


def report(book, *, day):
    fresh = book.freshness(day=day)
    return {"version": DRILLS_VERSION,
            "scenarios": len(DRILL_SCENARIOS),
            "runs": len(book.runs),
            "fresh": sum(1 for v in fresh.values()
                         if v["status"] == "fresh"),
            "stale": [k for k, v in fresh.items() if v["stale"]]}
