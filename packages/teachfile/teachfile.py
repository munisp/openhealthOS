"""Teaching files: anonymized case export for training.

Doctrine:
- Only VERIFIED reports teach. A draft is an opinion; a verified
  report is a finding. Contributing an unverified case teaches
  habits, not medicine.
- Consent is a basis, not a vibe: every contribution carries either
  explicit consent or an irreversible-anonymization basis, recorded
  with who and when.
- Anonymization is proven, not promised: the patient id never enters
  the teaching record (a hash does), the absolute day never leaves
  (a relative offset does), burned-in pixels refuse at the pacs
  export gate, and free text is scanned for the patient id before
  it is stored.
- The library does not erase: retraction (consent withdrawal) is
  append-only — the record stays, marked retracted, with a reason.
- Usage is measured with denominators: a teaching file nobody opens
  is shelf-decoration, and the report says so.
"""

import hashlib

TEACHFILE_VERSION = "teachfile@1.0.0"

CONSENT_BASES = ("explicit-consent", "anonymized-irreversible")

RETRACTION_REASONS = ("consent-withdrawn", "re-identification-risk",
                      "clinical-error", "duplicate")


class TeachFileError(Exception):
    pass


def _teaching_id(case_id, study_uid):
    return "TF-" + hashlib.sha256(
        f"{case_id}|{study_uid}".encode()).hexdigest()[:12].upper()


def _patient_hash(patient_id):
    return hashlib.sha256(f"patient|{patient_id}".encode()).hexdigest()[:12]


class TeachingLibrary:
    """Anonymized teaching-file library fed by verified teleread
    reports and pacs studies."""

    def __init__(self, reading_svc, pacs):
        self.svc = reading_svc
        self.pacs = pacs
        self.entries = {}       # teaching_id -> anonymized record
        self.uses = []          # append-only usage ledger
        self.retractions = []   # append-only retraction ledger

    # ---- contribution ----
    def contribute(self, case_id, study_uid, *, title, consent_basis,
                   contributed_by, day, tags=(), teaching_points=None):
        if consent_basis not in CONSENT_BASES:
            raise TeachFileError(
                f"consent basis must be one of {CONSENT_BASES} — "
                f"'assumed' is not a basis")
        if not title:
            raise TeachFileError("a teaching file needs a title")
        if case_id not in self.svc.cases:
            raise TeachFileError(f"unknown case: {case_id}")
        rep = self.svc.reports.get(case_id)
        if not rep or rep["state"] not in ("verified", "addended"):
            raise TeachFileError(
                "only verified reports teach — a draft is an opinion, "
                "not a finding")
        if study_uid not in self.pacs.studies:
            raise TeachFileError(f"unknown study: {study_uid}")
        study = self.pacs.studies[study_uid]
        patient_id = study["patient_id"]

        # pixel de-id gate: pacs refuses burned-in annotations
        self.pacs.export_metadata(study_uid, purpose="research")

        # free-text leak scan: the patient id must not appear anywhere
        # in what we are about to store
        payload = " ".join(
            [title, rep.get("findings", ""), rep.get("impression", ""),
             " ".join(tags), teaching_points or ""])
        if patient_id and patient_id in payload:
            raise TeachFileError(
                "patient identifier found in free text — anonymization "
                "is proven, not promised; strip it and resubmit")

        tid = _teaching_id(case_id, study_uid)
        if tid in self.entries:
            raise TeachFileError(f"{tid} already contributed — "
                                 "a second copy is not a second case")
        entry = {
            "teaching_id": tid,
            "title": title,
            "modality": study["modality"],
            "exam_protocol": study["exam_protocol"],
            "domain": self.svc.cases[case_id]["domain"],
            "findings": rep.get("findings", ""),
            "impression": rep.get("impression", ""),
            "template_version": rep.get("template_version"),
            "tags": tuple(tags),
            "teaching_points": teaching_points,
            "patient_hash": _patient_hash(patient_id),
            "case_age_days": day - study["day"],  # relative, never absolute
            "consent_basis": consent_basis,
            "contributed_by": contributed_by,
            "contributed_day": day,
            "version": TEACHFILE_VERSION,
            "retracted": False,
        }
        self.entries[tid] = entry
        return {"teaching_id": tid, "anonymized": True,
                "note": "no patient id, no absolute day, no burned-in "
                        "pixels — what is not stored cannot leak"}

    # ---- library ----
    def library(self, *, modality=None, tag=None, include_retracted=False):
        out = []
        for e in self.entries.values():
            if e["retracted"] and not include_retracted:
                continue
            if modality and e["modality"] != modality:
                continue
            if tag and tag not in e["tags"]:
                continue
            out.append(dict(e))
        return sorted(out, key=lambda e: e["teaching_id"])

    # ---- usage ----
    def record_use(self, teaching_id, *, by, day, purpose):
        e = self._entry(teaching_id)
        if e["retracted"]:
            raise TeachFileError(
                f"{teaching_id} is retracted — a withdrawn consent "
                "is a stop, not a suggestion")
        if not purpose:
            raise TeachFileError("usage requires a purpose")
        self.uses.append({"teaching_id": teaching_id, "by": by,
                          "day": day, "purpose": purpose})
        return {"recorded": True}

    # ---- retraction (append-only) ----
    def retract(self, teaching_id, *, by, day, reason):
        e = self._entry(teaching_id)
        if reason not in RETRACTION_REASONS:
            raise TeachFileError(
                f"retraction reason must be one of {RETRACTION_REASONS}")
        if e["retracted"]:
            raise TeachFileError(f"{teaching_id} already retracted")
        e["retracted"] = True
        self.retractions.append({"teaching_id": teaching_id, "by": by,
                                 "day": day, "reason": reason})
        return {"retracted": True,
                "note": "the record stays, marked — the lab notebook "
                        "does not erase"}

    # ---- reporting with denominators ----
    def usage_report(self):
        total = len(self.entries)
        live = sum(1 for e in self.entries.values() if not e["retracted"])
        per_entry = {}
        for u in self.uses:
            per_entry[u["teaching_id"]] = per_entry.get(u["teaching_id"], 0) + 1
        never_used = sum(1 for e in self.entries.values()
                         if not e["retracted"]
                         and e["teaching_id"] not in per_entry)
        return {
            "version": TEACHFILE_VERSION,
            "contributed": total,
            "live": live,
            "retracted": total - live,
            "total_uses": len(self.uses),
            "entries_never_used": {
                "n": never_used,
                "of": live,
                "rate": round(never_used / live, 3) if live else None,
                "note": "a teaching file nobody opens is "
                        "shelf-decoration",
            },
            "per_entry_uses": per_entry,
        }

    def _entry(self, teaching_id):
        if teaching_id not in self.entries:
            raise TeachFileError(f"unknown teaching file: {teaching_id}")
        return self.entries[teaching_id]
