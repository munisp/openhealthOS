"""vacapture.py — verbal autopsy capture for deaths outside facilities.

Most under-5 deaths in Nigeria happen at home, unseen by any clinician.
A verbal autopsy (VA) is a structured interview with the family that
lets the system learn *why* children die where nobody was watching.
This module is the capture layer only:

Doctrine:
- ADVISORY CAUSES, NOT DIAGNOSES. probable_cause() returns hints derived
  from reported symptoms for the data steward — a VA algorithm's output
  is never a death certificate. The hint is labelled advisory everywhere.
- FAIL-CLOSED COMPLETENESS: a VA form missing required sections cannot
  be submitted; partial interviews are saved as drafts and counted.
- CONSENT FIRST: interviewing a bereaved family without recorded
  consent is refused, not warned about.
- Aggregate export only — no line-list leaves the facility boundary.
"""

REQUIRED_SECTIONS = ("identity", "consent", "symptoms", "care_seeking")
MIN_INTERVIEW_DAY_GAP = 0  # culturally configurable; 0 = no enforced wait

# Symptom clusters -> advisory hint. Clean-room, deliberately simple:
# these are triage hints for the steward, tuned for the big three
# under-5 killers, NOT a validated algorithm (e.g. not InterVA).
_HINT_RULES = (
    ({"convulsions", "fever", "unconscious"}, "possible-severe-malaria-or-meningitis"),
    ({"fast-breathing", "chest-indrawing", "cough"}, "possible-pneumonia"),
    ({"diarrhoea", "sunken-eyes", "lethargy"}, "possible-diarrhoeal-disease"),
    ({"poor-feeding", "swelling", "wasting"}, "possible-severe-acute-malnutrition"),
    ({"fever", "rash"}, "possible-measles"),
    ({"prematurity", "poor-feeding", "fever"}, "possible-neonatal-sepsis-or-prematurity"),
)


class VAError(Exception):
    pass


class VARegistry:
    def __init__(self):
        self.forms = {}          # death_id -> form dict
        self.submitted = set()   # death_ids with complete forms

    def start(self, death_id, *, interviewer, day):
        if death_id in self.forms:
            raise VAError(f"VA already started for {death_id}")
        self.forms[death_id] = {
            "death_id": death_id, "interviewer": interviewer,
            "started_day": day, "consent": False,
            "sections": {}, "draft": True,
        }
        return dict(self.forms[death_id])

    def record_consent(self, death_id, *, consented, witness, day):
        f = self._form(death_id)
        f["consent"] = bool(consented)
        f["consent_witness"] = witness
        f["consent_day"] = day
        f["sections"]["consent"] = {"consented": bool(consented)}
        return dict(f)

    def fill_section(self, death_id, section, data):
        if section not in REQUIRED_SECTIONS:
            raise VAError(f"unknown section: {section}")
        f = self._form(death_id)
        if section == "consent":
            raise VAError("consent goes through record_consent")
        f["sections"][section] = dict(data)
        return dict(f)

    def submit(self, death_id):
        """Fail-closed: no consent or missing section -> refused."""
        f = self._form(death_id)
        if not f.get("consent"):
            raise VAError("no recorded consent — interview refused")
        missing = [s for s in REQUIRED_SECTIONS if s not in f["sections"]]
        if missing:
            raise VAError(f"incomplete VA, missing: {', '.join(missing)}")
        if not f["sections"]["symptoms"]:
            raise VAError("empty symptoms section — nothing to learn from")
        f["draft"] = False
        self.submitted.add(death_id)
        return dict(f)

    def probable_cause(self, death_id):
        """ADVISORY hint from reported symptoms. Never a diagnosis."""
        if death_id not in self.submitted:
            raise VAError("VA not submitted — no hint without a complete form")
        symptoms = set(self.forms[death_id]["sections"]["symptoms"].get("reported", []))
        hints = [label for needed, label in _HINT_RULES if needed <= symptoms]
        return {"death_id": death_id, "advisory": True,
                "hints": hints or ["undetermined-from-reported-symptoms"]}

    def aggregate(self):
        """What leaves the facility: counts only, never line-lists."""
        cause_counts = {}
        for did in self.submitted:
            for h in self.probable_cause(did)["hints"]:
                cause_counts[h] = cause_counts.get(h, 0) + 1
        drafts = sum(1 for f in self.forms.values() if f["draft"])
        refused = sum(1 for f in self.forms.values()
                      if f["draft"] and f["sections"].get("consent", {}).get("consented") is False)
        return {"interviews_started": len(self.forms),
                "submitted": len(self.submitted),
                "drafts": drafts,
                "consent_refused": refused,
                "advisory_cause_counts": cause_counts,
                "note": "advisory hints only — not certified causes of death"}

    def _form(self, death_id):
        if death_id not in self.forms:
            raise VAError(f"unknown death: {death_id}")
        return self.forms[death_id]
