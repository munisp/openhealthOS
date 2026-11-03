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


# ------------------------------------------------------------------
# Long-duration monitors: Holter and event monitors. A Holter is not
# a long 12-lead: it is a RECORDING with a duration contract, a
# burden calculation, and episode annotations.
HOLTER_KINDS = {"holter-24h": 24, "holter-48h": 48,
                "holter-72h": 72, "event-monitor-7d": 24 * 7,
                "event-monitor-14d": 24 * 14, "event-monitor-30d": 24 * 30}
HOLTER_MIN_USABLE_FRAC = 0.75   # <75% of nominal duration = repeat,
                                # not a negative
EPISODE_KINDS = ("af", "svt", "vt", "bradycardia", "pause",
                 "patient-symptom", "artifact")


class HolterService:
    """Long-duration ECG monitoring: wear, analyzable-hours contract,
    episode annotation, and burden math with denominators."""

    def __init__(self):
        self.monitors = {}

    def start_monitor(self, monitor_id, *, patient_id, kind, device_id,
                      facility, day):
        if kind not in HOLTER_KINDS:
            raise TeleReadError(f"unknown monitor kind: {kind}")
        if monitor_id in self.monitors:
            raise TeleReadError(f"duplicate monitor: {monitor_id}")
        self.monitors[monitor_id] = {
            "monitor_id": monitor_id, "patient_id": patient_id,
            "kind": kind, "device_id": device_id, "facility": facility,
            "start_day": day, "nominal_hours": HOLTER_KINDS[kind],
            "episodes": [], "analysis": None, "state": "worn"}
        return dict(self.monitors[monitor_id])

    def end_monitor(self, monitor_id, *, day, usable_hours):
        m = self._mon(monitor_id)
        if m["state"] != "worn":
            raise TeleReadError(f"{monitor_id} is {m['state']}")
        if not (0 < usable_hours <= m["nominal_hours"] * 1.1):
            raise TeleReadError(
                f"implausible usable hours: {usable_hours} for a "
                f"{m['kind']}")
        frac = usable_hours / m["nominal_hours"]
        m["usable_hours"] = usable_hours
        m["end_day"] = day
        # less than the usable fraction is NOT a negative study — it
        # is a repeat. Recording 'no events' on 3 hours of 24 is a lie.
        m["state"] = "analyzable" if frac >= HOLTER_MIN_USABLE_FRAC \
            else "insufficient-recording"
        return {"monitor_id": monitor_id, "state": m["state"],
                "usable_fraction": round(frac, 3),
                "note": ("insufficient recording is a repeat, not a "
                         "negative" if m["state"] ==
                         "insufficient-recording" else
                         "analyzable recording")}

    def annotate_episode(self, monitor_id, *, kind, day, minute,
                         duration_s, note=""):
        m = self._mon(monitor_id)
        if kind not in EPISODE_KINDS:
            raise TeleReadError(f"unknown episode kind: {kind}")
        if not (0 <= minute <= m["nominal_hours"] * 60):
            raise TeleReadError("episode minute outside the recording "
                                "window — an episode outside the tape "
                                "did not happen on this tape")
        if not (0 < duration_s <= 24 * 3600):
            raise TeleReadError(f"implausible episode duration: "
                                f"{duration_s}s")
        m["episodes"].append({"kind": kind, "day": day,
                              "minute": minute, "duration_s": duration_s,
                              "note": note})
        return len(m["episodes"])

    def record_analysis(self, monitor_id, *, by, day, total_beats,
                        min_hr, max_hr, avg_hr, af_burden_pct=0.0,
                        pause_max_s=0.0, vt_runs=0):
        """Analysis of an analyzable recording. Burdens are percentages
        with the denominator implicit in usable_hours — both shown.
        Flags propose; the cardiologist decides."""
        m = self._mon(monitor_id)
        if m["state"] == "worn":
            raise TeleReadError("still worn — analysis waits for the "
                                "recording to end")
        if m["state"] == "insufficient-recording":
            raise TeleReadError(
                "insufficient recording — repeat the monitor; "
                "analysing 3 hours of 24 as if it were a study is how "
                "AF gets missed")
        if m["analysis"] is not None:
            raise TeleReadError("already analysed — corrections are "
                                "the reading service's report versions")
        if not (0 <= min_hr <= avg_hr <= max_hr <= 300):
            raise TeleReadError("implausible heart-rate envelope")
        if not (0.0 <= af_burden_pct <= 100.0):
            raise TeleReadError("implausible AF burden")
        if not (0.0 <= pause_max_s <= 60.0):
            raise TeleReadError("implausible pause")
        flags = []
        if af_burden_pct > 0:
            flags.append(f"AF burden {af_burden_pct}% — stroke-risk "
                         "review (anticoagulation decision)")
        if pause_max_s >= 3.0:
            flags.append(f"pause {pause_max_s}s ≥ 3.0 — high-grade "
                         "block review")
        if vt_runs > 0:
            flags.append(f"{vt_runs} VT run(s) — urgent cardiology "
                         "review")
        m["analysis"] = {"by": by, "day": day, "total_beats": total_beats,
                         "min_hr": min_hr, "max_hr": max_hr,
                         "avg_hr": avg_hr,
                         "af_burden_pct": af_burden_pct,
                         "pause_max_s": pause_max_s,
                         "vt_runs": vt_runs, "flags": flags}
        m["state"] = "analyzed"
        return dict(m["analysis"])

    def monitor_report(self):
        """Programme vital signs: analyzability rate (a monitor that
        comes back unworn is a process finding), episode yields, AF
        detection — all with denominators."""
        total = len(self.monitors)
        states = {}
        for m in self.monitors.values():
            states[m["state"]] = states.get(m["state"], 0) + 1
        analyzed = [m for m in self.monitors.values()
                    if m["state"] == "analyzed"]
        af = sum(1 for m in analyzed
                 if m["analysis"]["af_burden_pct"] > 0)
        symptom_corr = sum(
            1 for m in analyzed
            for e in m["episodes"] if e["kind"] == "patient-symptom")
        return {"monitors": total, "by_state": states,
                "analyzable_rate": {
                    "n": states.get("analyzable", 0)
                         + states.get("analyzed", 0),
                    "of": total,
                    "rate": (round((states.get("analyzable", 0)
                                    + states.get("analyzed", 0))
                                   / total, 3) if total else None)},
                "af_detected": {"n": af, "of": len(analyzed),
                                "rate": (round(af / len(analyzed), 3)
                                         if analyzed else None)},
                "symptom_episodes": symptom_corr,
                "note": "insufficient recordings count against the "
                        "process, never against the patient"}

    def _mon(self, mid):
        if mid not in self.monitors:
            raise TeleReadError(f"unknown monitor: {mid}")
        return self.monitors[mid]


# ------------------------------------------------------------------
# Machine-interpretation edit trail (MUSE-style): the device's auto-read
# arrives WITH the tracing; the cardiologist confirms or edits; the
# delta between machine and human is the device's trust signal.
MACHINE_CODES = ("sinus-rhythm", "sinus-brady", "sinus-tachy",
                 "af", "flutter", "stemi", "st-elevation", "st-depression",
                 "t-inversion", "first-degree-block", "rbbb", "lbbb",
                 "lvh", "q-waves", "paced-rhythm", "artifact",
                 "ectopy", "normal")


class MachineEditTrail:
    """Machine vs human interpretation, per tracing, append-only.
    A machine read the human silently overwrote is unauditable; a
    machine read nobody reviewed is unearned trust."""

    def __init__(self, ecg_svc):
        self.svc = ecg_svc
        self.machine_reads = {}
        self.final_reads = {}

    def record_machine_interpretation(self, tracing_id, *, text, codes,
                                      sw_version):
        self.svc._tracing(tracing_id)
        unknown = set(codes) - set(MACHINE_CODES)
        if unknown:
            raise TeleReadError(f"unknown machine codes: "
                                f"{sorted(unknown)}")
        if not sw_version:
            raise TeleReadError("the machine's software version is "
                                "part of its read — an unversioned "
                                "algorithm is an unaccountable one")
        if tracing_id in self.machine_reads:
            raise TeleReadError("machine read already recorded — the "
                                "first read stays on the record")
        self.machine_reads[tracing_id] = {
            "text": text, "codes": tuple(sorted(codes)),
            "sw_version": sw_version}
        return dict(self.machine_reads[tracing_id])

    def record_final_interpretation(self, tracing_id, *, text, codes,
                                    by, day):
        if tracing_id not in self.machine_reads:
            raise TeleReadError("no machine read on record — the edit "
                                "trail needs its baseline (record the "
                                "device's read even when it is wrong)")
        unknown = set(codes) - set(MACHINE_CODES)
        if unknown:
            raise TeleReadError(f"unknown codes: {sorted(unknown)}")
        if not by:
            raise TeleReadError("unsigned interpretations are no "
                                "interpretation")
        if tracing_id in self.final_reads:
            raise TeleReadError("final read already recorded — "
                                "corrections are addenda")
        m = self.machine_reads[tracing_id]
        mset, fset = set(m["codes"]), set(codes)
        added = sorted(fset - mset)
        removed = sorted(mset - fset)
        rec = {"text": text, "codes": tuple(sorted(codes)), "by": by,
               "day": day, "edited": bool(added or removed),
               "codes_added": added, "codes_removed": removed}
        self.final_reads[tracing_id] = rec
        return dict(rec)

    def edit_trail(self, tracing_id):
        if tracing_id not in self.machine_reads:
            raise TeleReadError(f"no trail for {tracing_id}")
        return {"machine": dict(self.machine_reads[tracing_id]),
                "final": (dict(self.final_reads[tracing_id])
                          if tracing_id in self.final_reads else None),
                "note": "the delta between machine and human is the "
                        "device's trust signal"}

    def concordance_report(self):
        """Machine-human agreement with denominators — by software
        version, because an algorithm update is a new machine."""
        total = len(self.final_reads)
        agreed = sum(1 for f in self.final_reads.values()
                     if not f["edited"])
        per_sw = {}
        for tid, f in self.final_reads.items():
            sw = self.machine_reads[tid]["sw_version"]
            d = per_sw.setdefault(sw, {"n": 0, "unedited": 0})
            d["n"] += 1
            d["unedited"] += 0 if f["edited"] else 1
        for sw, d in per_sw.items():
            d["agreement_rate"] = round(d["unedited"] / d["n"], 3)
        return {"final_reads": total, "unedited": agreed,
                "agreement_rate": (round(agreed / total, 3)
                                   if total else None),
                "per_sw_version": per_sw,
                "note": "a machine nobody edits is either perfect or "
                        "unread — the per-version rate tells you which"}
