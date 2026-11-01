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

    def _scan(self, sid):
        if sid not in self.scans:
            raise TeleReadError(f"unknown scan: {sid}")
        return self.scans[sid]
