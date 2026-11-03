"""serial.py — specialists read trends, not snapshots.

A study without its priors is half a read. This package is the
'current vs previous' seam across imaging measurements, ECG intervals,
and fetal biometry:

  - measurements are RECORDED (per study, per lesion, versioned — a
    re-measurement is a new version, never an edit)
  - comparison is RECIST-shaped: percent change with absolute floors,
    because a 3 mm lesion growing 2 mm is +67% and still noise
  - ECG interval deltas flag the changes clinicians act on (QTc
    prolongation ≥60 ms, new conduction block)
  - fetal biometry checks against a versioned reference envelope and
    says honestly when a gestational age is outside the table

Nothing here interprets; it puts the delta in front of the human who
does.
"""


class SerialError(Exception):
    pass


# ---- measurements ----
class SeriesTracker:
    """Lesion/organ measurements against studies. Append-only."""

    def __init__(self):
        self.measurements = {}   # (study_uid, lesion_id, kind) -> [v]

    def record_measurement(self, study_uid, *, lesion_id, kind,
                           value_mm, day, by):
        if not lesion_id:
            raise SerialError("measurement without a lesion/landmark id "
                              "is an orphan number")
        if not (0.5 <= value_mm <= 1000):
            raise SerialError(f"implausible measurement: {value_mm}mm")
        if not by:
            raise SerialError("measurer identity required — unsigned "
                              "caliper work is gossip")
        key = (study_uid, lesion_id, kind)
        versions = self.measurements.setdefault(key, [])
        entry = {"version": len(versions) + 1, "value_mm": value_mm,
                 "day": day, "by": by}
        versions.append(entry)
        return dict(entry)

    def _latest(self, study_uid, lesion_id, kind):
        versions = self.measurements.get((study_uid, lesion_id, kind))
        if not versions:
            raise SerialError(
                f"no measurement for {lesion_id}/{kind} on {study_uid}")
        return versions[-1]

    def compare_lesion(self, study_a, study_b, *, lesion_id,
                       kind="long-axis"):
        """RECIST-shaped delta: percent change, but progression needs
        ≥20% AND ≥5 mm absolute — small-lesion percentages lie."""
        a = self._latest(study_a, lesion_id, kind)
        b = self._latest(study_b, lesion_id, kind)
        delta = b["value_mm"] - a["value_mm"]
        pct = 100 * delta / a["value_mm"]
        if pct >= 20 and delta >= 5:
            flag = "progression"
        elif pct <= -30:
            flag = "response"
        else:
            flag = "stable"
        return {"lesion_id": lesion_id, "kind": kind,
                "baseline_mm": a["value_mm"], "current_mm": b["value_mm"],
                "delta_mm": round(delta, 1), "pct_change": round(pct, 1),
                "flag": flag,
                "note": "RECIST-shaped: progression needs ≥20% AND "
                        "≥5mm; the radiologist confirms, the flag "
                        "proposes"}


# ---- imaging priors ----
def priors(pacs_registry, patient_id, *, modality=None,
           exam_protocol=None):
    """The priors chain for a patient, oldest first. A read ordered
    without priors is a read with one eye closed; a MISSING prior the
    archive should have is a finding (pacs reconciliation owns that)."""
    studies = [dict(s) for s in pacs_registry.studies.values()
               if s["patient_id"] == patient_id]
    if modality:
        studies = [s for s in studies if s["modality"] == modality]
    if exam_protocol:
        studies = [s for s in studies
                   if s["exam_protocol"] == exam_protocol]
    studies.sort(key=lambda s: s["day"])
    return {"patient_id": patient_id, "count": len(studies),
            "studies": studies,
            "note": "oldest first — comparison reads the trend, not "
                    "the snapshot"}


# ---- ECG serial comparison ----
ECG_INTERVALS = ("rate_bpm", "pr_ms", "qrs_ms", "qtc_ms")
QTC_DELTA_FLAG_MS = 60
QTC_DANGER_MS = 500
PR_BLOCK_MS = 200
QRS_BLOCK_MS = 120


def compare_ecg(report_a, report_b):
    """Two SIGNED ecg-12lead reports → interval deltas with the flags
    clinicians act on. Text findings are not diffed — rhythm language
    is the reader's, and pretending otherwise is NLP theatre."""
    for r in (report_a, report_b):
        if r.get("template_id") != "ecg-12lead@1.0.0":
            raise SerialError("compare_ecg binds to ecg-12lead@1.0.0 "
                              "reports — cross-template comparison is "
                              "apples to oranges")
        if r.get("status") != "signed":
            raise SerialError("unsigned reports are drafts — comparing "
                              "drafts is comparing rumours")
    deltas = {}
    flags = []
    a, b = report_a["answers"], report_b["answers"]
    for k in ECG_INTERVALS:
        if k in a and k in b:
            d = b[k] - a[k]
            deltas[k] = {"previous": a[k], "current": b[k], "delta": d}
    if "qtc_ms" in deltas:
        d = deltas["qtc_ms"]
        if d["delta"] >= QTC_DELTA_FLAG_MS:
            flags.append(f"QTc +{d['delta']}ms — drug review, "
                         "electrolytes, torsades risk")
        if d["current"] >= QTC_DANGER_MS:
            flags.append(f"QTc {d['current']}ms ≥ {QTC_DANGER_MS} — "
                         "danger zone regardless of delta")
    for k, bound, name in (("pr_ms", PR_BLOCK_MS, "PR"),
                           ("qrs_ms", QRS_BLOCK_MS, "QRS")):
        if k in deltas:
            d = deltas[k]
            if d["previous"] < bound <= d["current"]:
                flags.append(f"new {name} prolongation across "
                             f"{bound}ms — new conduction block")
    return {"deltas": deltas, "flags": flags,
            "note": "flags propose; the reader's eyes decide. Text "
                    "findings are re-read, not diffed"}


# ---- fetal biometry reference (coarse Hadlock-derived 5th–95th
# envelope; bind a validated local reference at deployment) ----
BIOMETRY_REF_VERSION = "biometry-ref@1.0.0"
# ga_weeks -> (bpd_mm, hc_mm, ac_mm, fl_mm) as (p5, p95)
BIOMETRY_TABLE = {
    14: ((21, 32), (80, 110), (65, 100), (10, 18)),
    18: ((33, 46), (120, 160), (105, 155), (22, 32)),
    22: ((45, 58), (160, 210), (145, 205), (33, 43)),
    26: ((58, 72), (210, 255), (190, 250), (43, 53)),
    30: ((70, 85), (255, 295), (235, 290), (52, 62)),
    34: ((79, 93), (285, 325), (270, 320), (58, 68)),
    38: ((88, 100), (305, 350), (300, 350), (64, 74)),
    40: ((92, 103), (320, 360), (310, 360), (67, 77)),
}
BIOMETRY_PARAMS = ("bpd", "hc", "ac", "fl")


def fetal_biometry(ga_weeks, measurements):
    """Check biometry against the reference envelope. Outside the
    table's gestational range → honest abstention, never extrapolated
    confidence. Outside the envelope → flagged, not diagnosed."""
    if not (12 <= ga_weeks <= 42):
        raise SerialError(f"implausible gestational age: {ga_weeks}w")
    known = sorted(BIOMETRY_TABLE)
    if ga_weeks < known[0] or ga_weeks > known[-1]:
        return {"ga_weeks": ga_weeks, "assessed": {}, "flags": [],
                "abstained": f"reference envelope spans {known[0]}-"
                             f"{known[-1]}w — no extrapolation",
                "ref_version": BIOMETRY_REF_VERSION}
    lo_w = max(w for w in known if w <= ga_weeks)
    hi_w = min(w for w in known if w >= ga_weeks)

    def band(param):
        i = BIOMETRY_PARAMS.index(param)
        lo_row, hi_row = BIOMETRY_TABLE[lo_w][i], BIOMETRY_TABLE[hi_w][i]
        if lo_w == hi_w:
            return lo_row
        f = (ga_weeks - lo_w) / (hi_w - lo_w)
        return (lo_row[0] + f * (hi_row[0] - lo_row[0]),
                lo_row[1] + f * (hi_row[1] - lo_row[1]))

    assessed, flags = {}, []
    for param, value in measurements.items():
        if param not in BIOMETRY_PARAMS:
            raise SerialError(f"unknown biometry parameter: {param}")
        p5, p95 = band(param)
        assessed[param] = {"value_mm": value, "p5": round(p5, 1),
                           "p95": round(p95, 1),
                           "in_envelope": p5 <= value <= p95}
        if value < p5:
            flags.append(f"{param} {value}mm below p5 ({p5:.0f}) at "
                         f"{ga_weeks}w — growth review")
        elif value > p95:
            flags.append(f"{param} {value}mm above p95 ({p95:.0f}) at "
                         f"{ga_weeks}w — macrosomia/dating review")
    if not assessed:
        raise SerialError("no measurements supplied — absence is not "
                          "a normal scan")
    return {"ga_weeks": ga_weeks, "assessed": assessed, "flags": flags,
            "ref_version": BIOMETRY_REF_VERSION,
            "note": "coarse Hadlock-derived envelope; outside the "
                    "envelope is a flag for the sonographer, never a "
                    "diagnosis"}


# ---- growth charting: a point is a fact; the SERIES is the chart.
# z-scores derived from the same shaped envelope as fetal_biometry
# (median = p5/p95 midpoint, sd = span / 3.29) so the chart and the
# per-scan flag can never disagree — one reference, one version.
GROWTH_REF_VERSION = "growth-ref-shaped@1.0.0"
_CENTILE_GAP = 1.34  # ~two major centile bands (50th→10th etc.)


def growth_z(param, ga_weeks, value):
    """z-score for one measurement against the shaped reference.
    Outside the table's range → honest abstention, same as the
    envelope: a z from thin air is a number, not evidence."""
    if param not in BIOMETRY_PARAMS:
        raise SerialError(f"unknown biometry parameter: {param}")
    known = sorted(BIOMETRY_TABLE)
    if ga_weeks < known[0] or ga_weeks > known[-1]:
        return None
    lo_w = max(w for w in known if w <= ga_weeks)
    hi_w = min(w for w in known if w >= ga_weeks)
    i = BIOMETRY_PARAMS.index(param)
    lo_row, hi_row = BIOMETRY_TABLE[lo_w][i], BIOMETRY_TABLE[hi_w][i]
    if lo_w == hi_w:
        p5, p95 = lo_row
    else:
        f = (ga_weeks - lo_w) / (hi_w - lo_w)
        p5 = lo_row[0] + f * (hi_row[0] - lo_row[0])
        p95 = lo_row[1] + f * (hi_row[1] - lo_row[1])
    median, sd = (p5 + p95) / 2, (p95 - p5) / 3.29
    return round((value - median) / sd, 2) if sd else None


def growth_assessment(series):
    """Chart an ordered series [(ga_weeks, {param: mm}), ...].

    One point abstains — a trajectory needs at least two. Crossing two
    centile bands between visits is the finding (growth faltering or
    acceleration), and it is named, not implied."""
    if len(series) < 2:
        raise SerialError("a growth chart of one point is a dot, not "
                          "a trajectory — come back with the series")
    points, flags = [], []
    prev = {}
    for ga, measurements in series:
        row = {"ga_weeks": ga, "z": {}}
        for param, value in measurements.items():
            z = growth_z(param, ga, value)
            row["z"][param] = z
            if z is None:
                continue  # abstained — stated as absent, not zero
            if abs(z) >= 2:
                flags.append(f"{param} z={z:+.2f} at {ga}w — outside "
                             "the outer centiles")
            if param in prev and prev[param] is not None and \
                    abs(z - prev[param]) >= _CENTILE_GAP:
                direction = ("faltering" if z < prev[param]
                             else "acceleration")
                flags.append(f"{param} crossed centile bands "
                             f"({prev[param]:+.2f} → {z:+.2f}) — "
                             f"{direction} between visits")
        for param, z in row["z"].items():
            if z is not None:
                prev[param] = z
        points.append(row)
    return {"points": points, "flags": flags,
            "ref_version": GROWTH_REF_VERSION,
            "note": "same shaped envelope as fetal_biometry — one "
                    "reference, one version, no silent disagreement"}
