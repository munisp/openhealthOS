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


# ------------------------------------------------------------------
# Lesion mapping / total-body photography: melanoma surveillance is a
# MAP, not a pile of case photos. Each tracked lesion has a body-map
# zone, a baseline image, and serial images against it; a changing
# lesion flags, and surveillance intervals are overdue-first.
LESIONMAP_VERSION = "lesionmap@1.0.0"
BODY_ZONES = (
    "scalp", "face", "neck",
    "chest", "abdomen", "back-upper", "back-lower",
    "arm-left-upper", "arm-left-fore", "hand-left",
    "arm-right-upper", "arm-right-fore", "hand-right",
    "leg-left-thigh", "leg-left-calf", "foot-left",
    "leg-right-thigh", "leg-right-calf", "foot-right",
    "genital", "buttock",
)
RISK_TIERS = {"standard": 365, "high": 180, "very-high": 90}  # days
CHANGE_FLAGS = {"size_growth_mm": 2.0, "new_colour": True,
                "ulceration": True}


class LesionMap:
    """Per-patient body map for surveillance. Doctrine: an unmapped
    lesion in a high-risk patient is a lesion nobody is watching;
    a changed lesion is a flag, not a verdict; and the interval runs
    from the last COMPLETE map, not from the last lucky photo."""

    def __init__(self):
        self.maps = {}       # patient_id -> {"tier":..., "last_map_day":...}
        self.lesions = {}    # (patient_id, lesion_id) -> record

    def enroll(self, patient_id, *, risk_tier, day, by):
        if risk_tier not in RISK_TIERS:
            raise TeleReadError(f"unknown risk tier: {risk_tier}")
        if patient_id in self.maps:
            raise TeleReadError(f"{patient_id} already mapped — "
                                "re-mapping is a new map, not a "
                                "rewrite")
        self.maps[patient_id] = {"risk_tier": risk_tier,
                                 "enrolled_day": day,
                                 "last_map_day": day}
        return dict(self.maps[patient_id])

    def map_lesion(self, patient_id, lesion_id, *, zone, image_ref,
                   day, by, size_mm=None, colours=()):
        m = self._map(patient_id)
        if not image_ref:
            raise TeleReadError("a mapped lesion needs its baseline "
                                "image — a pin without a photo is a "
                                "rumour")
        key = (patient_id, lesion_id)
        if key in self.lesions:
            raise TeleReadError(f"duplicate lesion: {lesion_id}")
        if zone not in BODY_ZONES:
            raise TeleReadError(f"unknown body zone: {zone} — the map "
                                "vocabulary is versioned")
        if size_mm is not None and not (0.1 <= size_mm <= 500):
            raise TeleReadError(f"implausible lesion size: {size_mm}mm")
        self.lesions[key] = {
            "lesion_id": lesion_id, "zone": zone, "images": [
                {"image_ref": image_ref, "day": day, "by": by,
                 "size_mm": size_mm, "colours": tuple(colours)}]}
        return dict(self.lesions[key])

    def add_image(self, patient_id, lesion_id, *, image_ref, day, by,
                  size_mm=None, colours=(), ulcerated=False):
        """Serial image against the baseline. Change flags: growth
        ≥2mm, a new colour, new ulceration — flags propose, the
        dermatologist decides."""
        key = (patient_id, lesion_id)
        if key not in self.lesions:
            raise TeleReadError(f"unmapped lesion: {lesion_id} — map "
                                "it before trending it")
        if not image_ref:
            raise TeleReadError("no image — nothing to compare")
        L = self.lesions[key]
        base = L["images"][0]
        flags = []
        if size_mm is not None and base["size_mm"] is not None:
            growth = size_mm - base["size_mm"]
            if growth >= CHANGE_FLAGS["size_growth_mm"]:
                flags.append(f"growth +{growth:.1f}mm from baseline "
                             f"{base['size_mm']}mm")
        new_colours = set(colours) - set(base["colours"])
        if new_colours:
            flags.append(f"new colour(s): {sorted(new_colours)}")
        if ulcerated:
            flags.append("ulceration")
        L["images"].append({"image_ref": image_ref, "day": day,
                            "by": by, "size_mm": size_mm,
                            "colours": tuple(colours),
                            "ulcerated": ulcerated, "flags": flags})
        return {"lesion_id": lesion_id, "n_images": len(L["images"]),
                "flags": flags}

    def complete_map(self, patient_id, *, day, by):
        m = self._map(patient_id)
        m["last_map_day"] = day
        return dict(m)

    def surveillance_due(self, *, as_of):
        """High-risk patients past their interval, most-overdue
        first."""
        out = []
        for pid, m in self.maps.items():
            interval = RISK_TIERS[m["risk_tier"]]
            overdue = as_of - m["last_map_day"] - interval
            if overdue > 0:
                out.append({"patient_id": pid,
                            "risk_tier": m["risk_tier"],
                            "days_overdue": overdue})
        return sorted(out, key=lambda x: -x["days_overdue"])

    def map_report(self):
        n_lesions = len(self.lesions)
        changed = sum(1 for L in self.lesions.values()
                      if any(img.get("flags") for img in L["images"][1:]))
        per_tier = {}
        for m in self.maps.values():
            per_tier[m["risk_tier"]] = per_tier.get(m["risk_tier"], 0) + 1
        return {"patients_mapped": len(self.maps),
                "lesions_tracked": n_lesions,
                "lesions_changed": {"n": changed, "of": n_lesions,
                                    "rate": (round(changed / n_lesions, 3)
                                             if n_lesions else None)},
                "per_tier": per_tier,
                "version": LESIONMAP_VERSION,
                "note": "the interval runs from the last COMPLETE map, "
                        "not the last lucky photo"}

    def _map(self, pid):
        if pid not in self.maps:
            raise TeleReadError(f"{pid} not enrolled — an unmapped "
                                "patient has no map")
        return self.maps[pid]
