"""aigov.py — AI/LLM/CV governance: model registry, licence classes,
draft-only outputs, override-rate monitoring.

Doctrine, applied to models:
  - licence classes decide WHERE a model may run:
      embed-clean     (Apache/MIT/BSD)  → may ship in-platform
      compose-only    (GPL/LGPL/AGPL)   → external process/service only
      research-only   (CC-BY-NC etc.)   → shadow mode ONLY, never shown
                                          to clinicians (MMS precedent)
  - every model output is a DRAFT stamped with model + version;
    an unversioned model's opinion is unauditable and refused
  - humans confirm or overrule; the override rate per model version is
    a monitored metric with denominators — a model whose agreement
    collapses gets suspended, not silently ignored
  - a nudge never executes: no model output writes to the record
"""

LICENCE_CLASSES = ("embed-clean", "compose-only", "research-only")


class AIGovError(Exception):
    pass


class ModelRegistry:
    def __init__(self):
        self.models = {}      # (model_id, version) -> record
        self.drafts = {}      # case_id -> draft
        self.outcomes = []    # confirm/override events

    def register(self, model_id, *, version, task, licence_class,
                 evidence_ref):
        if licence_class not in LICENCE_CLASSES:
            raise AIGovError(f"unknown licence class: {licence_class}")
        if not version:
            raise AIGovError("unversioned models are unauditable")
        if not evidence_ref:
            raise AIGovError(
                "no validation evidence — a model without a paper/audit "
                "trail does not touch patients, not even as a draft")
        key = (model_id, version)
        if key in self.models:
            raise AIGovError(f"duplicate model version: {model_id}@{version}")
        self.models[key] = {
            "model_id": model_id, "version": version, "task": task,
            "licence_class": licence_class, "evidence_ref": evidence_ref,
            "suspended": False}
        return dict(self.models[key])

    def submit_draft(self, case_id, *, model_id, version, output,
                     show_to_clinician=True):
        m = self.models.get((model_id, version))
        if not m:
            raise AIGovError(f"unregistered model: {model_id}@{version}")
        if m.get("suspended"):
            raise AIGovError(f"{model_id}@{version} is suspended")
        if m["licence_class"] == "research-only" and show_to_clinician:
            raise AIGovError(
                "research-only licence (non-commercial) — shadow mode "
                "only; this draft must never be shown to a clinician")
        entry = {"case_id": case_id, "model_id": model_id,
                 "version": version, "output": output,
                 "status": "draft-awaiting-human",
                 "shadow": m["licence_class"] == "research-only"}
        self.drafts[case_id] = entry
        return dict(entry)

    def record_outcome(self, case_id, *, reviewer, agree, note=""):
        d = self.drafts.get(case_id)
        if not d or d["status"] != "draft-awaiting-human":
            raise AIGovError("no pending draft for this case")
        d["status"] = "confirmed" if agree else "overruled"
        d["review"] = {"by": reviewer, "agree": bool(agree), "note": note}
        self.outcomes.append({"model_id": d["model_id"],
                              "version": d["version"], "agree": bool(agree)})
        return dict(d)

    def agreement_rates(self):
        """Per-model-version agreement with denominators. Always both
        numbers; a rate without n is marketing."""
        by = {}
        for o in self.outcomes:
            k = (o["model_id"], o["version"])
            b = by.setdefault(k, {"n": 0, "agreed": 0})
            b["n"] += 1
            b["agreed"] += int(o["agree"])
        return {f"{m}@{v}": dict(b, agreement=round(b["agreed"] / b["n"], 3))
                for (m, v), b in by.items()}

    def suspend(self, model_id, version, *, reason, by):
        m = self.models.get((model_id, version))
        if not m:
            raise AIGovError(f"unregistered model: {model_id}@{version}")
        if not reason:
            raise AIGovError("suspension requires a recorded reason")
        m["suspended"] = True
        m["suspension"] = {"reason": reason, "by": by}
        return dict(m)
