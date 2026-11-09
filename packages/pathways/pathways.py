
"""pathways.py — care-pathway variance engine.

A clinical pathway is the declared plan for a condition (e.g.
'uncomplicated malaria, adult': confirm → treat → review day 3).
Reality deviates — stock-outs, comorbidities, patient choice. This
module makes deviation VISIBLE: every step is expected or varied,
every variance carries a reason, and facilities can compare their
variance profile against their own history.

Doctrine:
- PATHWAYS ARE REGISTRY ENTRIES: ratified four-eyes before use; a
  pathway that was never ratified is a suggestion, not a protocol.
- DEVIATION IS DATA, NOT BLAME: vary() requires a stated reason from
  a declared vocabulary — 'no reason' is refused.
- ABSENCE IS STATED: a step that never happened is 'not-done' with a
  reason, never silently absent from the record.
"""

VERSION = "pathways@1.0.0"

STEP_STATES = ("pending", "done", "varied", "not-done")
VARIANCE_REASONS = ("stock-out", "comorbidity", "patient-choice",
                    "severity-change", "equipment-down", "staffing",
                    "referral", "guideline-update")


class PathwayError(Exception):
    pass


class PathwayRegistry:
    """Ratified pathway templates. Propose and ratify are different
    hands — the hand that proposes never approves."""

    def __init__(self):
        self._templates = {}   # key -> {"steps": [...], "status": ...}

    def propose(self, key, *, steps, by):
        if not steps or not all(isinstance(s, str) and s for s in steps):
            raise PathwayError("a pathway is its ordered steps — none given")
        if key in self._templates:
            raise PathwayError(f"duplicate pathway: {key}")
        self._templates[key] = {"steps": list(steps), "status": "proposed",
                                "proposed_by": by}

    def ratify(self, key, *, by):
        t = self._templates.get(key)
        if t is None:
            raise PathwayError(f"unknown pathway: {key}")
        if t["proposed_by"] == by:
            raise PathwayError("four-eyes: the hand that proposes never ratifies")
        t["status"] = "ratified"
        t["ratified_by"] = by

    def template(self, key):
        t = self._templates.get(key)
        if t is None or t["status"] != "ratified":
            raise PathwayError(
                f"pathway '{key}' is not ratified — a template pathway "
                f"is a placeholder, not a protocol")
        return list(t["steps"])


class PathwayCase:
    """One patient walking one pathway."""

    def __init__(self, registry, pathway_key, *, patient_ref, day):
        self.steps = [{"step": s, "state": "pending", "reason": None}
                      for s in registry.template(pathway_key)]
        self.patient_ref = patient_ref
        self.pathway = pathway_key
        self.opened_day = day

    def _step(self, name):
        for s in self.steps:
            if s["step"] == name:
                return s
        raise PathwayError(f"no such step: {name}")

    def complete(self, step):
        s = self._step(step)
        if s["state"] != "pending":
            raise PathwayError(f"step '{step}' already {s['state']}")
        s["state"] = "done"

    def vary(self, step, *, reason, note=""):
        if reason not in VARIANCE_REASONS:
            raise PathwayError(
                f"variance needs a declared reason — got '{reason}'")
        s = self._step(step)
        if s["state"] != "pending":
            raise PathwayError(f"step '{step}' already {s['state']}")
        s["state"] = "varied"
        s["reason"] = reason
        s["note"] = note

    def not_done(self, step, *, reason):
        self.vary(step, reason=reason)
        self._step(step)["state"] = "not-done"

    def report(self):
        total = len(self.steps)
        done = sum(1 for s in self.steps if s["state"] == "done")
        varied = [s for s in self.steps if s["state"] == "varied"]
        missing = [s for s in self.steps if s["state"] == "not-done"]
        return {"pathway": self.pathway, "patient_ref": self.patient_ref,
                "steps": total, "done": done, "varied": len(varied),
                "not_done": len(missing), "pending": total - done - len(varied) - len(missing),
                "variance_reasons": [s["reason"] for s in varied + missing]}


def variance_profile(cases):
    """Facility-level: which reasons dominate? Sorted, stated."""
    counts = {}
    for c in cases:
        for r in c.report()["variance_reasons"]:
            counts[r] = counts.get(r, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
