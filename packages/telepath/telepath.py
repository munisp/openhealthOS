"""telepath.py — tele-pathology: specimen-to-report with chain of custody.

Pathology differs from radiology in one crucial way: the artifact must
be MADE (collect → fix → section → stain → scan) before it can be read.
Every transformation is a hand-off, and every hand-off is recorded.
A specimen that cannot be accounted for cannot produce a report.

Engine: teleread. This module owns specimen custody and pathology's
domain rules; the engine owns assignment, sessions, escalation.
"""

from teleread import (DomainConfig, ReadingService, TeleReadError)  # noqa: F401

SPECIMEN_TYPES = ("biopsy", "resection", "cytology-fna", "cytology-fluid",
                  "bone-marrow")
CUSTODY_STAGES = ("collected", "fixed", "processed", "sectioned",
                  "stained", "scanned")
FIXATION_MIN_HOURS = 6   # under-fixed tissue reads wrong

CONFIG = DomainConfig(
    "pathology",
    scopes=("histopathologist", "cytopathologist"),
    critical_findings=("malignant", "high-grade-dysplasia",
                       "granulomatous-inflammation"),
    artifact_kinds=("wsi", "static-images"),
    sla_hours={"stat": 24, "urgent": 72, "routine": 240})


class PathologyService:
    def __init__(self, readers):
        self.reading = ReadingService(CONFIG, readers)
        self.specimens = {}   # specimen_id -> custody record

    def register_specimen(self, specimen_id, *, patient_id, specimen_type,
                          site, collector, facility, day):
        if specimen_type not in SPECIMEN_TYPES:
            raise TeleReadError(f"unknown specimen type: {specimen_type}")
        if not site:
            raise TeleReadError("no anatomical site — an unsited biopsy "
                                "is an uninterpretable one")
        if specimen_id in self.specimens:
            raise TeleReadError(f"duplicate specimen: {specimen_id}")
        self.specimens[specimen_id] = {
            "specimen_id": specimen_id, "patient_id": patient_id,
            "type": specimen_type, "site": site, "facility": facility,
            "custody": [("collected", day, collector)],
            "state": "collected"}
        return dict(self.specimens[specimen_id])

    def advance(self, specimen_id, stage, *, day, by,
                fixation_hours=None):
        s = self._spec(specimen_id)
        idx = CUSTODY_STAGES.index(s["state"])
        if stage not in CUSTODY_STAGES:
            raise TeleReadError(f"unknown stage: {stage}")
        if CUSTODY_STAGES.index(stage) != idx + 1:
            raise TeleReadError(
                f"custody is sequential: {s['state']} → {stage} skips "
                "steps; a skipped step is a broken chain")
        if stage == "processed":
            if fixation_hours is None:
                raise TeleReadError("fixation hours required before "
                                    "processing — record, don't guess")
            if fixation_hours < FIXATION_MIN_HOURS:
                raise TeleReadError(
                    f"under-fixed ({fixation_hours}h < "
                    f"{FIXATION_MIN_HOURS}h): refuse processing; "
                    "under-fixed tissue reads wrong")
        s["custody"].append((stage, day, by))
        s["state"] = stage
        return dict(s)

    def submit_for_reading(self, specimen_id, *, case_id, priority,
                           question, referrer, day, artifact):
        s = self._spec(specimen_id)
        if s["state"] != "scanned":
            raise TeleReadError(
                f"specimen is {s['state']} — only scanned specimens "
                "enter the reading queue")
        return self.reading.order(
            case_id, priority=priority, question=question,
            facility=s["facility"], referrer=referrer, day=day,
            scope=("cytopathologist" if s["type"].startswith("cytology")
                   else "histopathologist"),
            artifact=artifact,
            meta={"specimen_id": specimen_id, "site": s["site"],
                  "specimen_type": s["type"]})

    def reject_inadequate(self, specimen_id, *, day, by, reason):
        """An inadequate specimen is a finding, not a deletion."""
        if not reason:
            raise TeleReadError("rejection requires a reason")
        s = self._spec(specimen_id)
        s["state"] = "inadequate"
        s["custody"].append(("rejected", day, by))
        s["rejection_reason"] = reason
        return dict(s)

    def adequacy_rate(self):
        total = len(self.specimens)
        inad = sum(1 for s in self.specimens.values()
                   if s["state"] == "inadequate")
        return {"specimens": total, "inadequate": inad,
                "note": "adequacy = (specimens - inadequate)/specimens; "
                        "both shown"}

    def _spec(self, sid):
        if sid not in self.specimens:
            raise TeleReadError(f"unknown specimen: {sid}")
        return self.specimens[sid]
