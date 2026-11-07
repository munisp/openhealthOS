"""charting — long-form clinical charting: note templates, signed notes,
and order sets.

Where the point-of-care PWA is task-typed and protocol-guided, charting
is the physician's long-form surface: structured sections plus free
text, specialty templates, and order sets. This is the depth layer.

Doctrine:
- A TEMPLATE IS AN ENTRY, NEVER A BRANCH: adding a specialty adds an
  entry to NOTE_TEMPLATES; no code changes per specialty.
- SIGNED MEANS SIGNED: a signed note is frozen; corrections are addenda
  that cite the original, never edits. The audit chain is the chart.
- ABSENCE IS STATED: signing refuses while a required section is empty —
  a silent gap in a chart is a liability, an empty-stated one is a fact.
- FOUR-EYES ON ORDER SETS: the hand that proposes an order set never
  approves it; unratified sets refuse to apply.
- SAFETY GATES AT APPLICATION: an order set item that conflicts with a
  recorded allergy or age bound is REFUSED WITH THE REASON STATED —
  never silently dropped, never forced through.
"""

from __future__ import annotations

import hashlib
import json

CHARTING_VERSION = "charting@1.0.0"


class ChartingError(Exception):
    pass


def _refuse(msg):
    raise ChartingError(msg)


# ---------------------------------------------------------------------
# Note templates — the specialty pack is data.
# kind: "text" (free text) | "list" (bullet items) | "vitals" (k/v pairs)
# sensitive: withheld from cross-border summaries unless consent says
# otherwise (mental health, sexual health).
# ---------------------------------------------------------------------

NOTE_TEMPLATES = {
    "soap-general@1.0.0": {
        "label": "SOAP — general consultation",
        "specialty": "general",
        "sensitive": False,
        "status": "ratified",
        "sections": {
            "subjective": {"required": True, "kind": "text"},
            "objective": {"required": True, "kind": "text"},
            "assessment": {"required": True, "kind": "text"},
            "plan": {"required": True, "kind": "list"},
        },
    },
    "anc-visit@1.0.0": {
        "label": "Antenatal visit",
        "specialty": "obstetrics",
        "sensitive": False,
        "status": "ratified",
        "sections": {
            "gestational-age-weeks": {"required": True, "kind": "text"},
            "blood-pressure": {"required": True, "kind": "vitals"},
            "fundal-height": {"required": True, "kind": "text"},
            "fetal-heart": {"required": False, "kind": "text"},
            "danger-signs-reviewed": {"required": True, "kind": "list"},
            "plan": {"required": True, "kind": "list"},
        },
    },
    "pediatric-triage@1.0.0": {
        "label": "Paediatric triage",
        "specialty": "paediatrics",
        "sensitive": False,
        "status": "ratified",
        "sections": {
            "presenting-complaint": {"required": True, "kind": "text"},
            "vitals": {"required": True, "kind": "vitals"},
            "feeding-and-hydration": {"required": True, "kind": "text"},
            "danger-signs-reviewed": {"required": True, "kind": "list"},
            "imci-classification": {"required": True, "kind": "text"},
            "plan": {"required": True, "kind": "list"},
        },
    },
    "cardiology-review@1.0.0": {
        "label": "Cardiology review",
        "specialty": "cardiology",
        "sensitive": False,
        "status": "ratified",
        "sections": {
            "history": {"required": True, "kind": "text"},
            "examination": {"required": True, "kind": "text"},
            "ecg-findings": {"required": False, "kind": "text"},
            "echo-findings": {"required": False, "kind": "text"},
            "assessment": {"required": True, "kind": "text"},
            "plan": {"required": True, "kind": "list"},
        },
    },
    "surgical-note@1.0.0": {
        "label": "Operative note",
        "specialty": "surgery",
        "sensitive": False,
        "status": "ratified",
        "sections": {
            "procedure": {"required": True, "kind": "text"},
            "surgeon-and-team": {"required": True, "kind": "list"},
            "anaesthesia": {"required": True, "kind": "text"},
            "findings": {"required": True, "kind": "text"},
            "estimated-blood-loss": {"required": True, "kind": "text"},
            "counts-confirmed": {"required": True, "kind": "text"},
            "post-op-plan": {"required": True, "kind": "list"},
        },
    },
    "mental-health@1.0.0": {
        "label": "Mental health consultation",
        "specialty": "psychiatry",
        "sensitive": True,          # withheld from summaries by default
        "status": "ratified",
        "sections": {
            "presentation": {"required": True, "kind": "text"},
            "mental-state-exam": {"required": True, "kind": "text"},
            "risk-assessment": {"required": True, "kind": "text"},
            "formulation": {"required": False, "kind": "text"},
            "plan": {"required": True, "kind": "list"},
        },
    },
}


def register_template(template_id, *, label, specialty, sections,
                      sensitive=False, status="ratified"):
    """Adding a specialty is adding an entry — never a code branch."""
    if not template_id or not label or not sections:
        _refuse("a template names itself, its label, and its sections")
    if template_id in NOTE_TEMPLATES:
        _refuse("duplicate template id — version it instead")
    for name, spec in sections.items():
        if spec.get("kind") not in ("text", "list", "vitals"):
            _refuse(f"section {name!r} has unknown kind — kinds are "
                    "text, list, vitals")
    NOTE_TEMPLATES[template_id] = {
        "label": label, "specialty": specialty, "sensitive": sensitive,
        "status": status, "sections": dict(sections),
    }
    return template_id


class ChartNote:
    """One encounter note. Draft until signed; frozen thereafter."""

    def __init__(self, note_id, *, patient_ref, author, template_id, day):
        template = NOTE_TEMPLATES.get(template_id)
        if template is None:
            _refuse(f"unknown template {template_id!r}")
        if template["status"] != "ratified":
            _refuse("an unratified template is a draft form, not a chart")
        if not patient_ref or not author:
            _refuse("a note names its patient and its author")
        self.note_id = note_id
        self.patient_ref = patient_ref
        self.author = author
        self.template_id = template_id
        self.template = template
        self.day = day
        self.sections = {}
        self.signed_by = None
        self.signed_day = None
        self.addenda = []

    def set_section(self, name, value):
        if self.signed_day is not None:
            _refuse("signed notes are frozen — corrections are addenda")
        spec = self.template["sections"].get(name)
        if spec is None:
            _refuse(f"template {self.template_id!r} has no section "
                    f"{name!r} — the template is the contract")
        if spec["kind"] == "list" and not isinstance(value, list):
            _refuse(f"section {name!r} is a list section")
        if spec["kind"] == "vitals" and not isinstance(value, dict):
            _refuse(f"section {name!r} is a vitals (key/value) section")
        self.sections[name] = value
        return self

    def missing_required(self):
        return sorted(name for name, spec in self.template["sections"].items()
                      if spec["required"] and not self.sections.get(name))

    def sign(self, *, by, day):
        if self.signed_day is not None:
            _refuse("already signed")
        if by != self.author:
            _refuse("the author signs their own note — co-signing is an "
                    "addendum, not a signature")
        missing = self.missing_required()
        if missing:
            _refuse(f"required sections empty: {missing} — an empty "
                    "section is stated, never silent")
        self.signed_by = by
        self.signed_day = day
        return self

    def add_addendum(self, *, by, text, day):
        if self.signed_day is None:
            _refuse("addenda attach to signed notes; a draft is edited, "
                    "not amended")
        if not text:
            _refuse("an addendum says something")
        self.addenda.append({"by": by, "text": text, "day": day,
                             "amends": self.note_id})
        return self

    def fingerprint(self):
        body = {"note_id": self.note_id, "patient_ref": self.patient_ref,
                "author": self.author, "template_id": self.template_id,
                "sections": self.sections, "signed_by": self.signed_by,
                "signed_day": self.signed_day,
                "addenda": self.addenda}
        return hashlib.sha256(json.dumps(body, sort_keys=True)
                              .encode()).hexdigest()

    def as_dict(self):
        return {"note_id": self.note_id, "patient_ref": self.patient_ref,
                "author": self.author, "template_id": self.template_id,
                "template_label": self.template["label"],
                "sensitive": self.template["sensitive"],
                "sections": dict(self.sections),
                "signed_by": self.signed_by, "signed_day": self.signed_day,
                "addenda": [dict(a) for a in self.addenda],
                "sha256": self.fingerprint()}


# ---------------------------------------------------------------------
# Order sets — versioned, four-eyes, safety-gated at application.
# ---------------------------------------------------------------------

class OrderSetRegistry:
    def __init__(self):
        self.sets = {}
        self.audit = []

    def propose(self, set_id, *, version, label, items, by, day):
        """items: [{kind, label, ...safety fields}]
        kind "medication" carries allergy_class; any item may carry
        age_min_years / age_max_years bounds."""
        if not set_id or not label or not items:
            _refuse("an order set names itself, its label, its items")
        key = (set_id, version)
        if key in self.sets:
            _refuse("this order-set version exists — bump the version")
        for it in items:
            if not it.get("kind") or not it.get("label"):
                _refuse("every item has a kind and a label")
            if it["kind"] == "medication" and not it.get("allergy_class"):
                _refuse("a medication item names its allergy class — "
                        "the gate needs something to check")
        self.sets[key] = {"set_id": set_id, "version": version,
                          "label": label, "items": list(items),
                          "proposed_by": by, "proposed_day": day,
                          "ratified_by": None, "status": "proposed"}
        self.audit.append({"event": "order_set_proposed",
                           "set": set_id, "version": version,
                           "by": by, "day": day})
        return dict(self.sets[key])

    def ratify(self, set_id, *, version, by, day):
        s = self.sets.get((set_id, version))
        if s is None:
            _refuse("unknown order set")
        if by == s["proposed_by"]:
            _refuse("the hand that proposes never approves")
        s["ratified_by"] = by
        s["status"] = "ratified"
        self.audit.append({"event": "order_set_ratified", "set": set_id,
                           "version": version, "by": by, "day": day})
        return dict(s)

    def apply(self, set_id, *, version, patient, day):
        """Apply a ratified set to a patient. Every conflict is REFUSED
        WITH THE REASON STATED — nothing is silently dropped, nothing
        is forced through. Returns applied + refused lists."""
        s = self.sets.get((set_id, version))
        if s is None:
            _refuse("unknown order set")
        if s["status"] != "ratified":
            _refuse("an unratified order set is a draft, not an order")
        allergies = set(patient.get("allergies", ()))
        age = patient.get("age_years")
        applied, refused = [], []
        for it in s["items"]:
            if it["kind"] == "medication" and \
                    it["allergy_class"] in allergies:
                refused.append({**it, "reason":
                                f"allergy conflict: {it['allergy_class']}"})
                continue
            if age is None and ("age_min_years" in it or
                                "age_max_years" in it):
                refused.append({**it, "reason": "patient age unknown — "
                                "an age-bounded order refuses to guess"})
                continue
            if age is not None:
                if "age_min_years" in it and age < it["age_min_years"]:
                    refused.append({**it, "reason":
                                    f"below age floor {it['age_min_years']}"})
                    continue
                if "age_max_years" in it and age > it["age_max_years"]:
                    refused.append({**it, "reason":
                                    f"above age ceiling {it['age_max_years']}"})
                    continue
            applied.append(dict(it))
        self.audit.append({"event": "order_set_applied", "set": set_id,
                           "version": version, "day": day,
                           "applied": len(applied), "refused": len(refused)})
        return {"set_id": set_id, "version": version, "day": day,
                "applied": applied, "refused": refused,
                "stated": "refusals carry reasons — nothing drops silently"}

    def report(self):
        ratified = sum(1 for s in self.sets.values()
                       if s["status"] == "ratified")
        return {"version": CHARTING_VERSION, "sets": len(self.sets),
                "ratified": ratified, "audit_events": len(self.audit)}
