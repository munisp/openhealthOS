"""growthmon.py — growth monitoring & MUAC screening for under-5s.

Malnutrition is the quiet amplifier behind half of under-5 deaths —
it kills by making pneumonia and diarrhoea lethal. MUAC is the CHW's
instrument: one tape, no scales, no charts, no arithmetic.

Bands (publicly documented WHO/UNICEF thresholds):
  MUAC >= 12.5cm      green  — normal
  MUAC 11.5-12.4cm    yellow — moderate acute malnutrition risk, follow up
  MUAC < 11.5cm       red    — severe acute malnutrition, emergency referral

Doctrine:
- BILATERAL PITTING OEDEMA OVERRIDES THE TAPE: oedema = SAM (kwashiorkor)
  regardless of MUAC. The tape lies when the body holds water; the
  code refuses to.
- MUAC only valid 6-59 months; outside that range the module refuses
  rather than misclassifies.
- Weight trend is a secondary signal: two consecutive non-gains
  trigger a review flag. A single flat month is noise; the code
  doesn't alarm on noise.
- Every classification names its evidence (muac / oedema / trend).
"""

GREEN_MIN = 12.5
RED_MAX = 11.5
MIN_AGE_MONTHS = 6
MAX_AGE_MONTHS = 59
WEIGHT_STALL_VISITS = 2


class GrowthError(Exception):
    pass


def classify_muac(muac_cm, *, oedema=False, age_months=None):
    """One measurement -> band + action. Fail-closed on bad input."""
    if age_months is not None and not (MIN_AGE_MONTHS <= age_months <= MAX_AGE_MONTHS):
        raise GrowthError(
            f"MUAC valid only {MIN_AGE_MONTHS}-{MAX_AGE_MONTHS} months; "
            f"got {age_months}")
    if muac_cm is not None and not (5.0 <= muac_cm <= 25.0):
        raise GrowthError(f"implausible MUAC: {muac_cm}cm")
    if oedema:
        return {"band": "red", "classification": "severe-acute-malnutrition",
                "action": "emergency-referral",
                "evidence": ["bilateral-pitting-oedema"]}
    if muac_cm is None:
        raise GrowthError("no MUAC and no oedema — nothing to classify")
    if muac_cm < RED_MAX:
        return {"band": "red", "classification": "severe-acute-malnutrition",
                "action": "emergency-referral", "evidence": ["muac"]}
    if muac_cm < GREEN_MIN:
        return {"band": "yellow", "classification": "moderate-acute-malnutrition-risk",
                "action": "supplementary-feeding-followup", "evidence": ["muac"]}
    return {"band": "green", "classification": "normal",
            "action": "routine-prevention", "evidence": ["muac"]}


class GrowthChart:
    """Per-child measurement history with stall detection."""

    def __init__(self):
        self.children = {}  # child_id -> [measurements]

    def record(self, child_id, *, day, muac_cm=None, weight_kg=None,
               oedema=False, age_months=None):
        if weight_kg is not None and not (1.0 <= weight_kg <= 40.0):
            raise GrowthError(f"implausible weight: {weight_kg}kg")
        cls = classify_muac(muac_cm, oedema=oedema, age_months=age_months)
        hist = self.children.setdefault(child_id, [])
        stall = False
        if weight_kg is not None:
            prev = [m for m in hist if m.get("weight_kg") is not None]
            last = prev[-WEIGHT_STALL_VISITS:]
            if len(last) >= WEIGHT_STALL_VISITS and all(
                    weight_kg <= m["weight_kg"] for m in last):
                stall = True
        rec = {"day": day, "muac_cm": muac_cm, "weight_kg": weight_kg,
               "oedema": oedema, "classification": cls, "weight_stall": stall}
        hist.append(rec)
        out = dict(rec)
        if stall:
            out["review_flag"] = (
                f"no weight gain over {WEIGHT_STALL_VISITS} consecutive visits")
        return out

    def summary(self):
        reds = yellows = stalls = 0
        for hist in self.children.values():
            latest = hist[-1]["classification"]["band"]
            if latest == "red":
                reds += 1
            elif latest == "yellow":
                yellows += 1
            stalls += sum(1 for m in hist if m["weight_stall"])
        return {"children": len(self.children), "latest_red": reds,
                "latest_yellow": yellows, "weight_stall_events": stalls}
