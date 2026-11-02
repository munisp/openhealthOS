"""teleecg.py — tele-cardiology: ECG capture-to-interpretation.

The ECG machine sits at the PHC; the cardiologist doesn't. Device
provenance matters twice here: a waveform from an unverified device is
flagged, never laundered (devicereg doctrine), and a STEMI is a
critical finding that escalates until acknowledged.

Measurements (MEASUREMENT_VERSION): an ECG read without intervals is
not a read. PR/QRS/QTc/axis are recorded as data with plausibility
gates that refuse nonsense, the QTc correction method is always named
(a number without its formula is marketing), and corrections are
append-only — the first measurement stays on the record.

Engine: teleread. This module owns waveform ingest + domain rules.
"""

from teleread import DomainConfig, ReadingService, TeleReadError

LEAD_KINDS = ("12-lead", "3-lead", "single-lead")
CONFIG = DomainConfig(
    "ecg",
    scopes=("cardiac-reader",),
    critical_findings=("stemi", "complete-heart-block", "vt",
                       "severe-bradycardia"),
    artifact_kinds=LEAD_KINDS,
    sla_hours={"stat": 1, "urgent": 12, "routine": 48})


# ---- interval measurement rules (versioned data; thresholds are the
# kind a named cardiologist signs off at deployment) ----
MEASUREMENT_VERSION = "ecg-intervals@1.0.0"

QTC_METHODS = ("bazett", "fridericia", "framingham")

# (lo, hi) plausible envelopes — outside these the MACHINE or the
# transcription is wrong, and refusing loudly beats filing nonsense
PLAUSIBLE = {"rate_bpm": (20, 300), "pr_ms": (50, 500),
             "qrs_ms": (40, 300), "qt_ms": (150, 700),
             "qtc_ms": (250, 700), "rr_ms": (200, 3000)}
AXIS_RANGE = (-180, 270)

PR_PROLONGED_MS = 200       # first-degree AV block threshold
QRS_PROLONGED_MS = 120      # conduction delay threshold
QTC_BORDERLINE_MS = 450     # male upper-normal; female 470 — without
QTC_BORDERLINE_F_MS = 470   # a recorded sex we report both bounds
QTC_DANGER_MS = 500         # torsades risk zone
AXIS_NORMAL = (-30, 90)


def compute_qtc(qt_ms, rr_ms, *, method):
    """Corrected QT. The method is an argument, never a default —
    Bazett overcorrects at high rates and the record must say which
    lens produced the number."""
    if method not in QTC_METHODS:
        raise TeleReadError(f"unknown QTc method: {method} — "
                            f"a correction without a named formula is "
                            f"marketing")
    rr_s = rr_ms / 1000.0
    if method == "bazett":
        return round(qt_ms / (rr_s ** 0.5))
    if method == "fridericia":
        return round(qt_ms / (rr_s ** (1 / 3)))
    return round(qt_ms + 154 * (1 - rr_s))  # framingham


class ECGService:
    def __init__(self, readers, device_registry=None):
        self.reading = ReadingService(CONFIG, readers)
        self.devices = device_registry
        self.tracings = {}

    def record_tracing(self, tracing_id, *, patient_id, kind, device_id,
                       waveform_ref, facility, referrer, day):
        if kind not in LEAD_KINDS:
            raise TeleReadError(f"unknown ECG kind: {kind}")
        if not waveform_ref:
            raise TeleReadError("no waveform — nothing to read")
        flags = []
        if self.devices is not None:
            status = self.devices.reading_status(device_id, day)
            if status != "verified":
                flags.append(f"device-{status}")
        if tracing_id in self.tracings:
            raise TeleReadError(f"duplicate tracing: {tracing_id}")
        self.tracings[tracing_id] = {
            "tracing_id": tracing_id, "patient_id": patient_id,
            "kind": kind, "device_id": device_id,
            "waveform_ref": waveform_ref, "flags": flags, "day": day,
            "facility": facility, "measurements": []}
        return {k: v for k, v in self.tracings[tracing_id].items()
                if k != "measurements"}

    # ---- interval measurements: a read without intervals is not a
    # read ----
    def record_measurements(self, tracing_id, *, by, day, rate_bpm,
                            pr_ms, qrs_ms, qt_ms=None, rr_ms=None,
                            qtc_ms=None, qtc_method=None,
                            axis_deg=None, patient_sex=None):
        """Record the interval set. Fail-closed on implausibility;
        append-only (a correction is a new version, the old one stays
        on the record); every flag proposes — the reader decides."""
        t = self._tracing(tracing_id)
        if not by:
            raise TeleReadError("measurements need a measurer")
        values = {"rate_bpm": rate_bpm, "pr_ms": pr_ms, "qrs_ms": qrs_ms}
        if qt_ms is not None:
            values["qt_ms"] = qt_ms
        if rr_ms is not None:
            values["rr_ms"] = rr_ms
        for k, v in values.items():
            lo, hi = PLAUSIBLE[k]
            if not (lo <= v <= hi):
                raise TeleReadError(
                    f"implausible {k}: {v} (plausible {lo}-{hi}) — "
                    f"check the calipers or the transcription; "
                    f"nonsense does not get filed")

        # QTc: supplied (method mandatory) or computed from QT + RR
        if qtc_ms is None:
            if qt_ms is None or rr_ms is None:
                raise TeleReadError(
                    "QTc missing and no QT+RR to compute it — an ECG "
                    "read without QTc is unfinished")
            if qtc_method is None:
                raise TeleReadError(
                    "name the correction method — Bazett overcorrects "
                    "at high rates and the record must say which lens "
                    "produced the number")
            qtc_ms = compute_qtc(qt_ms, rr_ms, method=qtc_method)
        else:
            if qtc_method is None:
                raise TeleReadError(
                    "a supplied QTc must name its method")
            lo, hi = PLAUSIBLE["qtc_ms"]
            if not (lo <= qtc_ms <= hi):
                raise TeleReadError(f"implausible qtc_ms: {qtc_ms}")
        if qtc_method not in QTC_METHODS:
            raise TeleReadError(f"unknown QTc method: {qtc_method}")

        if axis_deg is not None:
            if t["kind"] != "12-lead":
                raise TeleReadError(
                    "electrical axis needs limb leads — a "
                    f"{t['kind']} tracing cannot carry one")
            lo, hi = AXIS_RANGE
            if not (lo <= axis_deg <= hi):
                raise TeleReadError(f"implausible axis: {axis_deg}")

        flags = []
        if qtc_ms >= QTC_DANGER_MS:
            flags.append(f"QTc {qtc_ms}ms ≥ {QTC_DANGER_MS} — danger "
                         "zone: drug review, electrolytes, torsades "
                         "risk")
        elif patient_sex is None:
            if qtc_ms >= QTC_BORDERLINE_F_MS:
                flags.append(f"QTc {qtc_ms}ms ≥ {QTC_BORDERLINE_F_MS} "
                             "— prolonged for either sex")
            elif qtc_ms >= QTC_BORDERLINE_MS:
                flags.append(f"QTc {qtc_ms}ms — between the male "
                             f"({QTC_BORDERLINE_MS}) and female "
                             f"({QTC_BORDERLINE_F_MS}) bounds; record "
                             "patient sex to resolve")
        else:
            bound = (QTC_BORDERLINE_F_MS if patient_sex == "female"
                     else QTC_BORDERLINE_MS)
            if qtc_ms >= bound:
                flags.append(f"QTc {qtc_ms}ms above {bound}ms bound — "
                             "borderline prolongation")
        if pr_ms > PR_PROLONGED_MS:
            flags.append(f"PR {pr_ms}ms > {PR_PROLONGED_MS} — "
                         "first-degree AV block pattern")
        if qrs_ms >= QRS_PROLONGED_MS:
            flags.append(f"QRS {qrs_ms}ms ≥ {QRS_PROLONGED_MS} — "
                         "conduction delay")
        if axis_deg is not None and not (AXIS_NORMAL[0] <= axis_deg
                                         <= AXIS_NORMAL[1]):
            side = ("left" if axis_deg < AXIS_NORMAL[0] else "right")
            flags.append(f"axis {axis_deg}° — {side} axis deviation")

        entry = {"v": len(t["measurements"]) + 1, "by": by, "day": day,
                 **values, "qtc_ms": qtc_ms, "qtc_method": qtc_method,
                 "axis_deg": axis_deg, "flags": flags,
                 "version": MEASUREMENT_VERSION}
        t["measurements"].append(entry)
        return dict(entry)

    def intervals(self, tracing_id):
        """Latest interval set + full history (corrections never erase
        the first read)."""
        t = self._tracing(tracing_id)
        if not t["measurements"]:
            raise TeleReadError(f"{tracing_id} has no measurements — "
                                "absence is not a normal ECG")
        return {"latest": dict(t["measurements"][-1]),
                "history": [dict(m) for m in t["measurements"]],
                "version": MEASUREMENT_VERSION}

    def submit_for_reading(self, tracing_id, *, case_id, priority,
                           question, referrer, day):
        t = self._tracing(tracing_id)
        return self.reading.order(
            case_id, priority=priority, question=question,
            facility=t["facility"],
            referrer=referrer, day=day, artifact=t["kind"],
            meta={"tracing_id": tracing_id, "kind": t["kind"],
                  "device_flags": t["flags"]})

    def _tracing(self, tid):
        if tid not in self.tracings:
            raise TeleReadError(f"unknown tracing: {tid}")
        return self.tracings[tid]
