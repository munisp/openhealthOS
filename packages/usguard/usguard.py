"""usguard.py — obstetric ultrasound QA: sampled over-read.

Midwives and CHOs scan; quality assurance over-reads a sample. The
point is not to read every scan — it is to keep scan quality honest
and catch the dangerous miss (ectopic, wrong dating) fast.

Doctrine: 100% of flagged-abnormal scans are over-read; a stated random
fraction of the rest; concordance per sonographer is feedback, never
punishment; a suspected ectopic escalates immediately, not at QA time.

Engine: teleread. This module owns the sampling policy and sonographer
concordance stats.
"""

import hashlib

from teleread import DomainConfig, ReadingService, TeleReadError

CONFIG = DomainConfig(
    "obstetric-us",
    scopes=("us-overreader",),
    critical_findings=("suspected-ectopic", "no-cardiac-activity",
                       "severe-oligohydramnios"),
    artifact_kinds=("cine", "static-images"),
    sla_hours={"stat": 4, "urgent": 24, "routine": 168})
DEFAULT_SAMPLE_PERCENT = 10

# ---- anomaly-scan protocol checklist (ISUOG 20+2-shaped) ----
# Versioned data: a mid-trimester scan that never looked at the outflow
# tracts is not an anomaly scan, it is a picture session.
ANOMALY_PROTOCOL_VERSION = "isuog-20-plus-2-shaped@1.0.0"
ANOMALY_PLANES = (
    # head & brain
    "transthalamic", "transventricular", "transcerebellar",
    # face
    "face-coronal-lips", "orbits",
    # chest & heart
    "four-chamber", "lvot", "rvot", "three-vessel-trachea",
    # abdomen
    "abdominal-situs-stomach", "cord-insertion", "kidneys", "bladder",
    # spine
    "spine-longitudinal", "spine-axial",
    # limbs
    "upper-limbs", "lower-limbs",
    # environment
    "placenta", "amniotic-fluid", "cervix",
)
PLANE_STATES = ("adequate", "inadequate", "not-visualized")
ANOMALY_GA_WINDOW = (18, 24)   # weeks; outside this it is not the
                               # anomaly scan and must not masquerade


class UltrasoundQA:
    def __init__(self, readers, sample_percent=DEFAULT_SAMPLE_PERCENT):
        if not (1 <= sample_percent <= 100):
            raise TeleReadError("sample percent must be 1..100")
        self.reading = ReadingService(CONFIG, readers)
        self.sample_percent = sample_percent
        self.scans = {}

    def register_scan(self, scan_id, *, patient_id, sonographer,
                      facility, gestational_weeks, image_ref, day,
                      flagged_abnormal=False):
        if scan_id in self.scans:
            raise TeleReadError(f"duplicate scan: {scan_id}")
        if gestational_weeks is None or not (4 <= gestational_weeks <= 45):
            raise TeleReadError(
                f"implausible gestational age: {gestational_weeks}")
        if not image_ref:
            raise TeleReadError("no image reference — nothing to QA")
        self.scans[scan_id] = {
            "scan_id": scan_id, "patient_id": patient_id,
            "sonographer": sonographer, "facility": facility,
            "ga_weeks": gestational_weeks, "image_ref": image_ref,
            "day": day, "flagged": bool(flagged_abnormal),
            "overread": None}
        return dict(self.scans[scan_id])

    def needs_overread(self, scan_id):
        """Deterministic sampling: ALL flagged scans, plus a stable
        random fraction (hash-seeded, so the sample is auditable)."""
        s = self._scan(scan_id)
        if s["flagged"]:
            return True
        h = int(hashlib.sha256(scan_id.encode()).hexdigest(), 16)
        return h % 100 < self.sample_percent

    def submit_overread(self, scan_id, *, case_id, referrer, day):
        s = self._scan(scan_id)
        if not self.needs_overread(scan_id):
            raise TeleReadError(
                f"{scan_id} not in the QA sample — do not inflate the "
                "denominator by reading only convenient scans")
        return self.reading.order(
            case_id, priority=("urgent" if s["flagged"] else "routine"),
            question=f"QA over-read, {s['ga_weeks']}w obstetric scan",
            facility=s["facility"], referrer=referrer, day=day,
            artifact="static-images",
            meta={"scan_id": scan_id, "sonographer": s["sonographer"]})

    def record_concordance(self, scan_id, *, agrees, discrepancy=None):
        s = self._scan(scan_id)
        if not agrees and not discrepancy:
            raise TeleReadError("discordance requires a named discrepancy")
        s["overread"] = {"agrees": bool(agrees), "discrepancy": discrepancy}
        return dict(s["overread"])

    def sonographer_concordance(self):
        """Per-sonographer agreement with denominators — feedback, not
        punishment."""
        by = {}
        for s in self.scans.values():
            if not s["overread"]:
                continue
            b = by.setdefault(s["sonographer"], {"n": 0, "agreed": 0})
            b["n"] += 1
            b["agreed"] += int(s["overread"]["agrees"])
        return {"by_sonographer": by,
                "note": "concordance = agreed/n per sonographer; "
                        "feedback data, never a league table"}

    # ---- anomaly-scan protocol completeness ----
    def record_anomaly_checklist(self, scan_id, *, planes, day, by):
        """The ISUOG-shaped 20-plane protocol, per-plane adequacy.
        Every plane accounted for — 'did not look' is not 'adequate'.
        An incomplete scan is not a failed patient: it earns a
        completion recall, loudly."""
        s = self._scan(scan_id)
        if not (ANOMALY_GA_WINDOW[0] <= s["ga_weeks"]
                <= ANOMALY_GA_WINDOW[1]):
            raise TeleReadError(
                f"anomaly protocol applies at "
                f"{ANOMALY_GA_WINDOW[0]}-{ANOMALY_GA_WINDOW[1]}w; this "
                f"scan is {s['ga_weeks']}w — do not masquerade a dating "
                "scan as an anomaly scan")
        if not by:
            raise TeleReadError("unsigned checklists are no checklist")
        if "checklist" in s:
            raise TeleReadError("checklist already recorded — the "
                                "completion scan is a NEW scan, not a "
                                "rewrite")
        unknown = set(planes) - set(ANOMALY_PLANES)
        if unknown:
            raise TeleReadError(f"unknown planes: {sorted(unknown)}")
        bad_states = {k: v for k, v in planes.items()
                      if v not in PLANE_STATES}
        if bad_states:
            raise TeleReadError(f"bad plane states: {bad_states}")
        missing = set(ANOMALY_PLANES) - set(planes)
        if missing:
            raise TeleReadError(
                f"checklist incomplete: {sorted(missing)} never "
                "assessed — an unassessed plane is not an adequate one")
        gaps = sorted(p for p in ANOMALY_PLANES
                      if planes[p] != "adequate")
        s["checklist"] = {"planes": dict(planes), "day": day, "by": by,
                          "gaps": gaps, "complete": not gaps,
                          "protocol": ANOMALY_PROTOCOL_VERSION}
        return dict(s["checklist"])

    def incomplete_anomaly_scans(self):
        """Scans with plane gaps — completion recall, oldest first."""
        out = [{"scan_id": sid, "patient_id": s["patient_id"],
                "sonographer": s["sonographer"], "day": s["day"],
                "gaps": s["checklist"]["gaps"]}
               for sid, s in self.scans.items()
               if s.get("checklist") and not s["checklist"]["complete"]]
        return sorted(out, key=lambda x: x["day"])

    def anomaly_report(self):
        """Protocol completeness with denominators — and per-plane gap
        counts, because the plane nobody gets right is a training
        finding, not a secret."""
        done = [s["checklist"] for s in self.scans.values()
                if s.get("checklist")]
        per_plane = {p: 0 for p in ANOMALY_PLANES}
        for c in done:
            for g in c["gaps"]:
                per_plane[g] += 1
        complete = sum(1 for c in done if c["complete"])
        return {"checklists": len(done), "complete": complete,
                "completeness_rate": (round(complete / len(done), 3)
                                      if done else None),
                "per_plane_gaps": {p: n for p, n in per_plane.items()
                                   if n},
                "protocol": ANOMALY_PROTOCOL_VERSION,
                "note": "the denominator is checklists recorded; a "
                        "plane's gap count is a training signal"}

    def _scan(self, sid):
        if sid not in self.scans:
            raise TeleReadError(f"unknown scan: {sid}")
        return self.scans[sid]
