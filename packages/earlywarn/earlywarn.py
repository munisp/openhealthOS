"""earlywarn.py — NEWS2 early-warning scoring with trend surfaces.

RCP NEWS2 (2017) as versioned data. A single score is a snapshot; the
trajectory is the finding. So the ledger is append-only, trends compare
against the patient's own baseline, and a partial score refuses — six
of seven parameters is a wrong score, not a smaller one.
"""


class EarlyWarnError(Exception):
    pass


NEWS2_VERSION = "news2@1.0.0"   # Royal College of Physicians, 2017

# vital name in the vitals package -> NEWS2 parameter
PARAM_VITALS = {"rr": "respiratory-rate", "spo2": "spo2",
                "temp_c": "temperature", "sbp": "systolic-bp",
                "hr": "heart-rate"}

BANDS = ((0, "low"), (4, "low"), (6, "medium"), (99, "high"))
# total 0: low; 1-4: low (ward-based); 5-6: medium (urgent response);
# 7+: high (emergency). Plus the RCP single-parameter rule: any one
# parameter scoring 3 demands review even when the total is low.


def _rr(v):
    if v <= 8:
        return 3
    if v <= 11:
        return 1
    if v <= 20:
        return 0
    if v <= 24:
        return 2
    return 3


def _spo2(v, scale2, on_oxygen):
    if not scale2:
        if v <= 91:
            return 3
        if v <= 93:
            return 2
        if v <= 95:
            return 1
        return 0
    # scale 2: hypercapnic respiratory failure target 88-92%
    if v <= 83:
        return 3
    if v <= 85:
        return 2
    if v <= 87:
        return 1
    if v <= 92:
        return 0
    if on_oxygen:
        if v <= 94:
            return 1
        if v <= 96:
            return 2
        return 3
    return 0   # hyperoxia without oxygen is not penalized


def _temp(v):
    if v <= 35.0:
        return 3
    if v <= 36.0:
        return 1
    if v <= 38.0:
        return 0
    if v <= 39.0:
        return 1
    return 2


def _sbp(v):
    if v <= 90:
        return 3
    if v <= 100:
        return 2
    if v <= 110:
        return 1
    if v <= 219:
        return 0
    return 3


def _hr(v):
    if v <= 40:
        return 3
    if v <= 50:
        return 1
    if v <= 90:
        return 0
    if v <= 110:
        return 1
    if v <= 130:
        return 2
    return 3


def news2(*, rr, spo2, temp_c, sbp, hr, avpu, on_oxygen=False,
          spo2_scale2=False):
    """One NEWS2 score. Every parameter required; bounds already
    enforced upstream by the vitals package."""
    if avpu not in ("A", "V", "P", "U"):
        raise EarlyWarnError("avpu must be A/V/P/U — consciousness is "
                             "not optional in an early-warning score")
    parts = {"rr": _rr(rr),
             "spo2": _spo2(spo2, spo2_scale2, on_oxygen),
             "temp_c": _temp(temp_c), "sbp": _sbp(sbp), "hr": _hr(hr),
             "avpu": 0 if avpu == "A" else 3,
             "oxygen": 2 if on_oxygen else 0}
    total = sum(parts.values())
    band = next(name for ceiling, name in BANDS if total <= ceiling)
    single3 = [p for p, v in parts.items() if v == 3]
    return {"score": total, "band": band, "parts": parts,
            "single_param_3": single3,
            "escalate": band in ("medium", "high") or bool(single3),
            "version": NEWS2_VERSION}


class EarlyWarner:
    """Scores from the vitals stream and keeps the trajectory."""

    def __init__(self, vitals_ingest):
        self.vitals = vitals_ingest
        self.ledger = {}   # patient_id -> [score events]

    def score_now(self, patient_id, *, day, ts, avpu, on_oxygen=False,
                  spo2_scale2=False):
        """Assemble the seven parameters from the patient's LATEST
        vitals. A missing vital refuses — a partial score is a wrong
        score, not a smaller one."""
        obs = {}
        missing = []
        for param, vital in PARAM_VITALS.items():
            latest = self.vitals.latest(patient_id, vital)
            if latest is None:
                missing.append(vital)
            else:
                obs[param] = latest["valueQuantity"]["value"]
        if missing:
            raise EarlyWarnError(
                f"no {', '.join(missing)} on record for {patient_id} — "
                "NEWS2 needs all seven parameters; a partial score is "
                "a wrong score")
        s = news2(avpu=avpu, on_oxygen=on_oxygen,
                  spo2_scale2=spo2_scale2, **obs)
        event = {"patient_id": patient_id, "day": day, "ts": ts,
                 **s}
        self.ledger.setdefault(patient_id, []).append(event)
        return dict(event)

    def trend(self, patient_id):
        """The trajectory, not the snapshot. One score abstains."""
        series = self.ledger.get(patient_id, [])
        if not series:
            raise EarlyWarnError(f"no scores on record for {patient_id}")
        if len(series) < 2:
            return {"patient_id": patient_id, "n": 1,
                    "direction": None,
                    "note": "one score is a snapshot — the trajectory "
                            "is the finding; score again"}
        first, last = series[0], series[-1]
        delta = last["score"] - first["score"]
        direction = ("rising" if delta > 0 else
                     "falling" if delta < 0 else "stable")
        _rank = {"low": 0, "medium": 1, "high": 2}
        crossed_up = _rank[last["band"]] > _rank[first["band"]]
        return {"patient_id": patient_id, "n": len(series),
                "first_score": first["score"], "last_score": last["score"],
                "delta": delta, "direction": direction,
                "band_now": last["band"], "crossed_band_upward": crossed_up,
                "escalate": last["escalate"]}

    def deteriorating(self, as_of, *, threshold=5):
        """Patients whose latest score meets the threshold or whose
        band is climbing — worst first."""
        out = []
        for pid, series in self.ledger.items():
            last = series[-1]
            t = self.trend(pid)
            if last["score"] >= threshold or t.get("crossed_band_upward"):
                out.append({"patient_id": pid, "score": last["score"],
                            "band": last["band"], "day": last["day"],
                            "direction": t.get("direction")})
        out.sort(key=lambda p: -p["score"])
        return out

    def report(self, as_of):
        latest = [s[-1] for s in self.ledger.values()]
        bands = {}
        for e in latest:
            bands[e["band"]] = bands.get(e["band"], 0) + 1
        return {"patients_scored": len(self.ledger),
                "score_events": sum(len(s) for s in self.ledger.values()),
                "latest_band_distribution": bands,
                "deteriorating_now": len(self.deteriorating(as_of)),
                "version": NEWS2_VERSION,
                "note": "the band distribution ships with its "
                        "denominator: patients_scored"}
