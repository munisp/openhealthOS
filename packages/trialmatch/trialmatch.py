
"""trialmatch.py — clinical trial eligibility matcher.

Research sites screen hundreds of charts by hand to fill a trial; the
patient who would have qualified is usually never found. This module
matches patient phenotypes against ratified trial criteria — and then
STOPS. Matching is not enrollment: the output is an advisory list for
a clinician to discuss with the patient. Consent is a human ceremony,
not a database flag.

Doctrine:
- CRITERIA ARE DECLARED AND VERSIONED: each trial's inclusion and
  exclusion rules are data, ratified, with the protocol version on
  every match output.
- MATCH IS ADVISORY: the engine returns candidates + which criteria
  passed/failed per patient. It cannot enroll, consent, or contact.
- EXCLUSION WINS TIES: any failed exclusion criterion removes the
  candidate regardless of inclusion score — safety first.
"""

VERSION = "trialmatch@1.0.0"


class TrialError(Exception):
    pass


class TrialRegistry:
    def __init__(self):
        self._trials = {}

    def register(self, trial_id, *, protocol_version, inclusion,
                 exclusion, site, ratified_by):
        """inclusion/exclusion: lists of {field, op, value} with op in
        eq/gte/lte/in. All data — the engine never evals code."""
        if not protocol_version:
            raise TrialError("protocol version required — criteria drift "
                             "without one")
        for rule in inclusion + exclusion:
            if rule.get("op") not in ("eq", "gte", "lte", "in"):
                raise TrialError(f"unsupported op: {rule.get('op')}")
        self._trials[trial_id] = {
            "protocol_version": protocol_version, "inclusion": inclusion,
            "exclusion": exclusion, "site": site, "ratified_by": ratified_by}

    def trial(self, trial_id):
        t = self._trials.get(trial_id)
        if t is None:
            raise TrialError(f"unknown trial: {trial_id}")
        return t


def _check(rule, phenotype):
    v = phenotype.get(rule["field"])
    if v is None:
        return None   # absence is not a value — criterion UNKNOWN
    op, target = rule["op"], rule["value"]
    if op == "eq":
        return v == target
    if op == "gte":
        return v >= target
    if op == "lte":
        return v <= target
    if op == "in":
        return v in target
    return None


def match(trial, phenotype):
    """Returns (verdict, detail). verdict in
    eligible / excluded / unknown (missing data stated)."""
    inc_detail, exc_detail, unknown = [], [], []
    for rule in trial["inclusion"]:
        r = _check(rule, phenotype)
        inc_detail.append({"rule": rule, "result": r})
        if r is not True:
            return ("excluded" if r is False else "unknown",
                    {"failed_inclusion": rule} if r is False
                    else {"unknown_inclusion": rule})
    for rule in trial["exclusion"]:
        r = _check(rule, phenotype)
        exc_detail.append({"rule": rule, "result": r})
        if r is True:
            return "excluded", {"hit_exclusion": rule}
        if r is None:
            unknown.append(rule)
    if unknown:
        return "unknown", {"unknown_exclusion": unknown}
    return "eligible", {"inclusion": inc_detail, "exclusion": exc_detail}


def screen(registry, trial_id, phenotypes):
    """phenotypes: {patient_ref: {field: value}}. Returns advisory list
    sorted eligible first; every verdict carries the protocol version."""
    t = registry.trial(trial_id)
    out = []
    for ref, pheno in sorted(phenotypes.items()):
        verdict, detail = match(t, pheno)
        out.append({"patient_ref": ref, "verdict": verdict,
                    "detail": detail,
                    "protocol_version": t["protocol_version"],
                    "note": "advisory match — enrollment requires clinician "
                            "discussion and written consent"})
    order = {"eligible": 0, "unknown": 1, "excluded": 2}
    out.sort(key=lambda r: order[r["verdict"]])
    return out
