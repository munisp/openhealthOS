"""telederm.py — store-and-forward dermatology with image consent.

A phone photo of a lesion is PHI twice over: it is identifiable by
nature (faces, tattoos) and it travels by chat if we let it. So image
consent is structural, NTD flags are a data list, and malignancy
suspicion is a critical finding on the engine's ladder.

Engine: teleread. This module owns consent + the lesion record.
"""

from teleread import DomainConfig, ReadingService, TeleReadError

NTD_FLAGS = ("leprosy-suspect", "buruli-suspect", "scabies",
             "tinea-capitis")
CONSENT_KINDS = ("signed", "verbal-witnessed")

CONFIG = DomainConfig(
    "dermatology",
    scopes=("derm-reader",),
    critical_findings=("suspected-melanoma", "suspected-scc"),
    artifact_kinds=("dermoscopy", "clinical-photo"),
    sla_hours={"stat": 24, "urgent": 72, "routine": 336})


class TeleDerm:
    def __init__(self, readers):
        self.reading = ReadingService(CONFIG, readers)
        self.lesions = {}

    def submit_lesion(self, case_id, *, patient_id, body_site, image_ref,
                      consent, facility, referrer, day, duration_days,
                      ntd_flags=()):
        if consent not in CONSENT_KINDS:
            raise TeleReadError(
                "image consent required (signed or verbal-witnessed) — "
                "a lesion photo is identifiable by nature")
        if not body_site:
            raise TeleReadError("body site required")
        if not image_ref:
            raise TeleReadError("no image — nothing to read")
        unknown = set(ntd_flags) - set(NTD_FLAGS)
        if unknown:
            raise TeleReadError(f"unknown NTD flags: {sorted(unknown)}")
        if duration_days is None or duration_days < 0:
            raise TeleReadError("lesion duration required")
        self.lesions[case_id] = {
            "patient_id": patient_id, "body_site": body_site,
            "consent": consent, "ntd_flags": list(ntd_flags),
            "duration_days": duration_days}
        priority = "urgent" if ntd_flags else "routine"
        return self.reading.order(
            case_id, priority=priority,
            question=f"lesion, {body_site}, {duration_days}d",
            facility=facility, referrer=referrer, day=day,
            artifact="clinical-photo",
            meta={"patient_id": patient_id, "body_site": body_site,
                  "consent": consent, "ntd_flags": list(ntd_flags)})

    def ntd_surveillance(self):
        """Aggregate only — flags feed EBS as rumour-not-alert."""
        counts = {}
        for l in self.lesions.values():
            for f in l["ntd_flags"]:
                counts[f] = counts.get(f, 0) + 1
        return {"counts": counts, "cases_flagged": len(self.lesions),
                "note": "aggregate-only; individual cases stay with "
                        "the care team"}
