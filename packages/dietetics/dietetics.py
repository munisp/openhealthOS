"""dietetics.py — therapeutic diets, nutrition screening, and kitchen orders.

Malnutrition is a diagnosis, not a mood. In a developing-country ward
the diet order is as clinical as the drug order: the child on F-75, the
TB patient on a high-protein build-up, the dialysis patient on fluid
restriction — these are prescriptions the kitchen must be able to read.

Doctrine:
- DIETS ARE REGISTERED ENTRIES: therapeutic diet codes (F-75, F-100,
  RUTF, high-protein, fluid-restricted, renal, diabetic, NPO) are a
  registry, not free text.
- SCREENING PRECEDES PRESCRIPTION: MUST-style screening (BMI, weight
  loss, intake) gates the order. You cannot prescribe F-100 to a child
  nobody screened.
- THE KITCHEN GETS AN ORDER, NOT A NOTE: every diet order generates a
  kitchen ticket with the day, the ward, and the number of portions.
- ALLERGY CROSS-CHECK IS FAIL-CLOSED: a diet containing a documented
  allergen refuses.
"""

import hashlib
import json

VERSION = "dietetics@1.0.0"

# therapeutic diet registry
DIETS = {
    "normal": {"description": "standard ward diet", "contraindications": []},
    "f-75": {"description": "therapeutic milk, starter phase (SAM)",
             "contraindications": []},
    "f-100": {"description": "therapeutic milk, rehabilitation phase (SAM)",
              "contraindications": []},
    "rutf": {"description": "ready-to-use therapeutic food",
             "contraindications": ["peanut-allergy"]},
    "high-protein": {"description": "high-protein build-up",
                     "contraindications": ["renal-failure-unless-dialysis"]},
    "fluid-restricted": {"description": "fluid restriction (renal/ cardiac)",
                         "contraindications": []},
    "renal": {"description": "renal diet (low K, low PO4, controlled protein)",
              "contraindications": []},
    "diabetic": {"description": "diabetic diet", "contraindications": []},
    "npo": {"description": "nil by mouth", "contraindications": []},
    "clear-fluids": {"description": "clear fluids only",
                     "contraindications": []},
    "soft": {"description": "soft diet", "contraindications": []},
}

# nutrition screening result -> recommended diet
_SCREEN_MAP = {
    "severe-acute-malnutrition": "f-75",
    "moderate-acute-malnutrition": "rutf",
    "at-risk": "high-protein",
    "normal": "normal",
}


class Error(Exception):
    pass


def register_diet(code, description, contraindications=None):
    if not code or not description:
        raise Error("a diet entry names its code and description")
    DIETS[code] = {"description": description,
                   "contraindications": contraindications or []}


class NutritionScreen:
    """MUST-style screen: BMI + weight loss + intake reduction."""

    def __init__(self, patient_ref, *, day, screener):
        self.patient_ref = patient_ref
        self.day = day
        self.screener = screener
        self.result = None

    def assess(self, *, bmi=None, weight_loss_pct=0.0,
               intake_reduced_days=0):
        if bmi is not None and not (8.0 <= bmi <= 80.0):
            raise Error(f"BMI {bmi} implausible — refused")
        score = 0
        if bmi is not None:
            if bmi < 18.5:
                score += 1
            if bmi < 16.0:
                score += 1
        if weight_loss_pct >= 10.0:
            score += 2
        elif weight_loss_pct >= 5.0:
            score += 1
        if intake_reduced_days >= 5:
            score += 2
        if score >= 4:
            result = "severe-acute-malnutrition" if (bmi or 30) < 16 \
                else "at-risk"
        elif score >= 2:
            result = "at-risk"
        elif score >= 1:
            result = "moderate-acute-malnutrition" if (bmi or 30) < 18.5 \
                else "at-risk"
        else:
            result = "normal"
        self.result = {"bmi": bmi, "weight_loss_pct": weight_loss_pct,
                       "intake_reduced_days": intake_reduced_days,
                       "score": score, "category": result,
                       "recommended_diet": _SCREEN_MAP[result]}
        return self.result


class DietOrder:
    """A prescription the kitchen can read."""

    def __init__(self, order_id, patient_ref, ward, *, day, prescriber,
                 screen: NutritionScreen, diet_code, portions_per_day=3,
                 allergies=()):
        if diet_code not in DIETS:
            raise Error(f"unknown diet: {diet_code} — register it first")
        if screen.result is None:
            raise Error("no diet order without a completed screen")
        # allergy cross-check — fail closed
        allergy_set = {a.lower() for a in allergies or ()}
        for contra in DIETS[diet_code]["contraindications"]:
            if contra.lower() in allergy_set:
                raise Error(
                    f"diet {diet_code} contraindicated: {contra} — "
                    "documented allergy")
        if not (1 <= portions_per_day <= 8):
            raise Error("implausible portions per day")
        if not prescriber:
            raise Error("a diet order names its prescriber")
        self.order_id = order_id
        self.patient_ref = patient_ref
        self.ward = ward
        self.day = day
        self.prescriber = prescriber
        self.diet_code = diet_code
        self.portions_per_day = portions_per_day
        self.screen_category = screen.result["category"]
        self.active = True

    def stop(self, *, by, day, reason):
        if not self.active:
            raise Error("order already stopped")
        if not (reason and reason.strip()):
            raise Error("stopping a diet order states why")
        self.active = False
        self.stopped = {"by": by, "day": day, "reason": reason}

    def kitchen_ticket(self):
        if not self.active:
            raise Error("order stopped — no kitchen ticket")
        return {"order_id": self.order_id, "ward": self.ward,
                "diet_code": self.diet_code,
                "portions": self.portions_per_day, "day": self.day,
                "description": DIETS[self.diet_code]["description"]}


class WardKitchenSheet:
    """One ward's day of diet orders — the kitchen's manifest."""

    def __init__(self, ward, day):
        self.ward = ward
        self.day = day
        self.orders = []

    def add(self, order):
        if order.ward != self.ward:
            raise Error("order belongs to a different ward")
        if not order.active:
            raise Error("stopped orders do not reach the kitchen")
        self.orders.append(order.kitchen_ticket())

    def manifest(self):
        by_diet = {}
        for o in self.orders:
            by_diet[o["diet_code"]] = by_diet.get(o["diet_code"], 0) \
                + o["portions"]
        body = {"ward": self.ward, "day": self.day, "orders": self.orders,
                "portions_by_diet": by_diet, "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
