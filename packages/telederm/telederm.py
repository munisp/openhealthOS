"""telederm.py — store-and-forward dermatology with image consent.

A phone photo of a lesion is PHI twice over: it is identifiable by
nature (faces, tattoos) and it travels by chat if we let it. So image
consent is structural, NTD flags are a data list, and malignancy
suspicion is a critical finding on the engine's ladder.

Engine: teleread. This module owns consent + the lesion record.
"""

from teleread import DomainConfig, ReadingService, TeleReadError
from duelgrade import CaptureQA

NTD_FLAGS = ("leprosy-suspect", "buruli-suspect", "scabies",
             "tinea-capitis")
CONSENT_KINDS = ("signed", "verbal-witnessed")

# capture-time dermoscopy/photo standards — telederm P0:
# a scale marker for size, focus sharp enough for margins, the whole
# lesion in frame, and a dermoscopic view (or a deliberate clinical-only
# view) so pigmented lesions are not judged from a flash photo.
DERM_CHECKS = ("scale-marker", "focus", "whole-lesion-in-frame",
               "dermoscopic-or-clinical-view")

# the two-week-wait cancer pathway — the metric the whole pathway
# exists for: suspicion → specialist seen within 14 days, tracked.
TWW_TARGET_DAYS = 14
PATHWAY_STAGES = ("suspected", "referred", "attended", "diagnosed")
DIAGNOSIS_CONFIRMED_BY = ("histology", "clinical")

CONFIG = DomainConfig(
    "dermatology",
    scopes=("derm-reader",),
    critical_findings=("suspected-melanoma", "suspected-scc"),
    artifact_kinds=("dermoscopy", "clinical-photo"),
    sla_hours={"stat": 24, "urgent": 72, "routine": 336})


class TeleDerm:
    def __init__(self, readers):
        self.reading = ReadingService(CONFIG, readers)
        self.qa = CaptureQA("derm", DERM_CHECKS)
        self.lesions = {}
        self.pathway = {}   # case_id -> {stage: day, ...}

    def assess_capture(self, image_ref, *, day, by, passed=(), failed=()):
        return self.qa.assess(image_ref, day=day, by=by,
                              passed=passed, failed=failed)

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
        if not self.qa.gradable(image_ref):
            raise TeleReadError(
                "no gradable capture QA on record — a lesion photo "
                "nobody quality-checked is a rumour with pixels; an "
                "ungradable photo is a failed consult, not a benign "
                "lesion")
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

    # ---- the two-week-wait cancer pathway ----
    def raise_suspicion(self, case_id, *, finding, day, by):
        """A named clinician raises a cancer suspicion on a lesion case;
        the 14-day clock starts NOW. 'Suspected' without a clock is a
        worry, not a pathway."""
        if case_id not in self.lesions:
            raise TeleReadError(f"unknown lesion case: {case_id}")
        if finding not in CONFIG.critical_findings:
            raise TeleReadError(
                f"'{finding}' is not a cancer-pathway finding — the "
                "clock is for suspected cancer, not everything worrying")
        if not by:
            raise TeleReadError("suspicion without a named clinician is "
                                "a rumour")
        if case_id in self.pathway:
            raise TeleReadError("pathway already open — the clock does "
                                "not restart to hide a breach")
        self.pathway[case_id] = {
            "patient_id": self.lesions[case_id]["patient_id"],
            "finding": finding, "by": by, "suspected": day,
            "target_day": day + TWW_TARGET_DAYS}
        return dict(self.pathway[case_id])

    def advance_pathway(self, case_id, stage, *, day, ref=None,
                        diagnosis=None, confirmed_by=None):
        p = self.pathway.get(case_id)
        if not p:
            raise TeleReadError("no open pathway — suspicion comes first")
        idx = PATHWAY_STAGES.index(stage)
        done = [s for s in PATHWAY_STAGES if s in p]
        if len(done) != idx:
            raise TeleReadError(
                f"the pathway is sequential: '{stage}' requires "
                f"'{PATHWAY_STAGES[idx - 1]}' first — an attendance "
                "without a referral is unverifiable")
        if stage == "referred" and not ref:
            raise TeleReadError("a referral without a reference id is a "
                                "letter nobody can trace")
        if stage == "diagnosed":
            if not diagnosis:
                raise TeleReadError("diagnosis required — the pathway "
                                    "ends in an answer, not a shrug")
            if confirmed_by not in DIAGNOSIS_CONFIRMED_BY:
                raise TeleReadError(
                    "diagnosis must say how it was confirmed: "
                    f"{DIAGNOSIS_CONFIRMED_BY}")
            p["diagnosis"] = diagnosis
            p["confirmed_by"] = confirmed_by
        if ref:
            p["referral_ref"] = ref
        p[stage] = day
        return dict(p)

    def two_week_breaches(self, as_of):
        """Past the 14-day target without attending — most overdue
        first. The breach list is the pathway's whole point."""
        out = []
        for cid, p in self.pathway.items():
            if "attended" in p:
                continue
            if p["target_day"] < as_of:
                out.append({"case_id": cid, "patient_id": p["patient_id"],
                            "finding": p["finding"],
                            "suspected": p["suspected"],
                            "target_day": p["target_day"],
                            "overdue_days": as_of - p["target_day"],
                            "stage": [s for s in PATHWAY_STAGES
                                      if s in p][-1]})
        out.sort(key=lambda b: (-b["overdue_days"], b["case_id"]))
        return out

    def pathway_report(self, as_of):
        n = len(self.pathway)
        attended = [p for p in self.pathway.values() if "attended" in p]
        within = [p for p in attended
                  if p["attended"] - p["suspected"] <= TWW_TARGET_DAYS]
        days = sorted(p["attended"] - p["suspected"] for p in attended)
        median = (days[len(days) // 2] if days else None)
        return {"suspected": n,
                "referred": sum(1 for p in self.pathway.values()
                                if "referred" in p),
                "attended": len(attended),
                "attended_within_14d": len(within),
                "within_target_rate": (round(len(within) / len(attended), 3)
                                       if attended else None),
                "median_days_to_attend": median,
                "breaches_now": len(self.two_week_breaches(as_of)),
                "note": "the rate's denominator is attendances; the "
                        "breach count's denominator is every suspicion "
                        "ever raised"}
