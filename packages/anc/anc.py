"""anc.py — antenatal contact schedule + obstetric danger signs.

MPDSR audits maternal deaths after they happen; this is the
prevention side. The eight-contact ANC model (WHO 2016, publicly
documented) gives the schedule; the danger-sign screen gives every
contact teeth.

Schedule (contact: window in gestational weeks):
  1: up to 12w   2: 20w   3: 26w   4: 30w   5: 34w   6: 36w
  7: 38w   8: 40w (windows +/-1-2w as per guidance, simplified here)

Doctrine:
- DANGER SIGNS ESCALATE FROM ANY CADRE, IMMEDIATELY: vaginal bleeding,
  severe headache/blurred vision (pre-eclampsia), fever, reduced fetal
  movement, leaking liquor, severe abdominal pain. A danger sign ends
  the routine contact and opens an emergency referral.
- HYPERTENSION IN PREGNANCY IS A RED FLAG, NOT A FOLLOW-UP: BP >=
  140/90 escalates to the hypertension pathway WITH pregnancy context.
- LATE BOOKING IS A FINDING: first contact after 12w is recorded as
  late booking — it predicts everything downstream and must be
  countable.
- MISSED CONTACTS SURFACE: a pregnancy past a contact window with no
  recorded contact goes on the overdue list, like everything else.
"""

CONTACTS = (
    {"contact": 1, "week": 12, "window": (0, 12)},
    {"contact": 2, "week": 20, "window": (13, 22)},
    {"contact": 3, "week": 26, "window": (23, 28)},
    {"contact": 4, "week": 30, "window": (29, 32)},
    {"contact": 5, "week": 34, "window": (33, 35)},
    {"contact": 6, "week": 36, "window": (36, 37)},
    {"contact": 7, "week": 38, "window": (38, 39)},
    {"contact": 8, "week": 40, "window": (40, 42)},
)
BOOKING_ON_TIME_WEEK = 12

DANGER_SIGNS = (
    "vaginal-bleeding", "severe-headache-or-blurred-vision", "fever",
    "reduced-fetal-movement", "leaking-liquor", "severe-abdominal-pain",
    "convulsions",
)
BP_SYSTOLIC_FLAG = 140
BP_DIASTOLIC_FLAG = 90


class ANCError(Exception):
    pass


class PregnancyRegister:
    def __init__(self):
        self.pregnancies = {}

    def register(self, pregnancy_id, *, lmp_day, day, facility,
                 high_risk=False, note=""):
        """lmp_day: platform day of last menstrual period."""
        if pregnancy_id in self.pregnancies:
            raise ANCError(f"duplicate pregnancy: {pregnancy_id}")
        if lmp_day > day:
            raise ANCError("LMP in the future?")
        ga_weeks = (day - lmp_day) // 7
        if ga_weeks > 45:
            raise ANCError(f"gestational age {ga_weeks}w implausible")
        self.pregnancies[pregnancy_id] = {
            "lmp_day": lmp_day, "registered_day": day,
            "facility": facility, "high_risk": bool(high_risk),
            "note": note, "contacts": {}, "outcome": None,
            "late_booking": ga_weeks > BOOKING_ON_TIME_WEEK,
        }
        return dict(self.pregnancies[pregnancy_id])

    def contact(self, pregnancy_id, contact_no, *, day, cadre,
                bp=None, danger_signs=(), hiv_tested=None,
                tt_dose=None, iptp_dose=None, note=""):
        """Record a contact. Returns escalate=True when any danger sign
        or BP flag fires — the contact is recorded AND the emergency
        pathway opens. iptp/tt doses recorded, never assumed."""
        p = self._preg(pregnancy_id)
        if p["outcome"]:
            raise ANCError("pregnancy already closed")
        spec = next((c for c in CONTACTS if c["contact"] == contact_no), None)
        if spec is None:
            raise ANCError(f"unknown contact: {contact_no}")
        if contact_no in p["contacts"]:
            raise ANCError(f"contact {contact_no} already recorded")
        unknown = set(danger_signs) - set(DANGER_SIGNS)
        if unknown:
            raise ANCError(f"unknown danger signs: {sorted(unknown)}")
        ga = (day - p["lmp_day"]) // 7
        on_time = spec["window"][0] <= ga <= spec["window"][1]
        bp_flag = False
        if bp is not None:
            sys_, dia = bp
            if not (50 <= sys_ <= 260 and 30 <= dia <= 160):
                raise ANCError(f"implausible BP: {bp}")
            bp_flag = sys_ >= BP_SYSTOLIC_FLAG or dia >= BP_DIASTOLIC_FLAG
        escalate = bool(danger_signs) or bp_flag
        reasons = list(danger_signs)
        if bp_flag:
            reasons.append(f"hypertension-in-pregnancy: {bp[0]}/{bp[1]}")
        rec = {"contact": contact_no, "day": day, "ga_weeks": ga,
               "cadre": cadre, "on_time": on_time, "bp": bp,
               "danger_signs": list(danger_signs),
               "hiv_tested": hiv_tested, "tt_dose": tt_dose,
               "iptp_dose": iptp_dose, "note": note,
               "escalate": escalate, "escalation_reasons": reasons}
        p["contacts"][contact_no] = rec
        return dict(rec)

    def close(self, pregnancy_id, *, day, outcome):
        """outcome: live-birth | stillbirth | miscarriage | maternal-death
        | transferred-out. maternal-death MUST link to MPDSR — the
        register refuses to close as maternal-death without an mpdsr id."""
        if outcome == "maternal-death":
            raise ANCError(
                "use close_maternal_death — the MPDSR death id is required")
        if outcome not in ("live-birth", "stillbirth", "miscarriage",
                           "maternal-death", "transferred-out"):
            raise ANCError(f"unknown outcome: {outcome}")
        p = self._preg(pregnancy_id)
        p["outcome"] = {"outcome": outcome, "day": day}
        return dict(p)

    def close_maternal_death(self, pregnancy_id, *, day, mpdsr_death_id):
        if not mpdsr_death_id:
            raise ANCError(
                "maternal-death closure requires the MPDSR death id — "
                "the death must exist in the audit system")
        p = self._preg(pregnancy_id)
        p["outcome"] = {"outcome": "maternal-death", "day": day,
                        "mpdsr_death_id": mpdsr_death_id}
        return dict(p)

    def overdue_contacts(self, as_of):
        out = []
        for pid, p in self.pregnancies.items():
            if p["outcome"]:
                continue
            ga = (as_of - p["lmp_day"]) // 7
            for c in CONTACTS:
                if c["contact"] in p["contacts"]:
                    continue
                if ga > c["window"][1]:
                    out.append({"pregnancy_id": pid, "contact": c["contact"],
                                "ga_weeks": ga, "facility": p["facility"],
                                "high_risk": p["high_risk"]})
                    break  # earliest missed contact is the work item
        return out

    def stats(self, as_of):
        active = [p for p in self.pregnancies.values() if not p["outcome"]]
        late = sum(1 for p in self.pregnancies.values() if p["late_booking"])
        escalations = sum(1 for p in self.pregnancies.values()
                          for c in p["contacts"].values() if c["escalate"])
        hiv_tested = sum(1 for p in self.pregnancies.values()
                         if any(c["hiv_tested"] for c in p["contacts"].values()))
        outcomes = {}
        for p in self.pregnancies.values():
            if p["outcome"]:
                k = p["outcome"]["outcome"]
                outcomes[k] = outcomes.get(k, 0) + 1
        return {"as_of": as_of, "registered": len(self.pregnancies),
                "active": len(active), "late_bookings": late,
                "hiv_tested_at_least_once": hiv_tested,
                "escalated_contacts": escalations,
                "overdue_contacts": len(self.overdue_contacts(as_of)),
                "outcomes": outcomes}

    def _preg(self, pid):
        if pid not in self.pregnancies:
            raise ANCError(f"unknown pregnancy: {pid}")
        return self.pregnancies[pid]
