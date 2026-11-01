"""cervixscreen.py — cervical cancer screen-and-treat.

The whole point of VIA screening is SAME-VISIT treatment: a woman who
travels for a screen and is told to 'come back' usually doesn't. So the
cascade is strict (screened → via-result → treated-or-referred) and a
positive screen without a recorded treatment decision is an open loop
that stays visible until closed.

Engine: teleread for image over-read; this module owns the cascade.
"""

from teleread import DomainConfig, ReadingService, TeleReadError

VIA_RESULTS = ("negative", "positive", "suspicious-of-cancer")
TREATMENTS = ("thermal-ablation", "leep", "cryotherapy")
CASCADE = ("screened", "result", "treated", "referred")

CONFIG = DomainConfig(
    "cervix-screening",
    scopes=("cervix-reviewer",),
    critical_findings=("suspicious-of-cancer",),
    artifact_kinds=("cervigram",),
    sla_hours={"stat": 24, "urgent": 72, "routine": 336})


class CervixScreening:
    def __init__(self, readers):
        self.reading = ReadingService(CONFIG, readers)
        self.cascade = {}   # patient_id -> record

    def screen(self, patient_id, *, facility, screener, day,
               image_ref=None, hiv_positive=False):
        if patient_id in self.cascade:
            raise TeleReadError(f"duplicate screen: {patient_id}")
        self.cascade[patient_id] = {
            "facility": facility, "screener": screener, "day": day,
            "image_ref": image_ref, "hiv_positive": bool(hiv_positive),
            "via_result": None, "treatment": None, "referred": False,
            "overread_case": None}
        rec = self.cascade[patient_id]
        if image_ref:
            rec["overread_case"] = self.reading.order(
                f"cx-{patient_id}-{day}", priority="routine",
                question="VIA cervigram over-read", facility=facility,
                referrer=screener, day=day, artifact="cervigram",
                meta={"patient_id": patient_id})["case_id"]
        return dict(rec)

    def record_via(self, patient_id, *, result, day):
        if result not in VIA_RESULTS:
            raise TeleReadError(f"unknown VIA result: {result}")
        c = self._c(patient_id)
        c["via_result"] = result
        c["result_day"] = day
        return dict(c)

    def treat(self, patient_id, *, method, day, same_visit=None):
        c = self._c(patient_id)
        if c["via_result"] != "positive":
            raise TeleReadError(
                "treatment requires a positive VIA — treating a negative "
                "screen is harm, not care")
        if method not in TREATMENTS:
            raise TeleReadError(f"unknown treatment: {method}")
        c["treatment"] = {"method": method, "day": day,
                          "same_visit": (day == c["result_day"]
                                         if same_visit is None
                                         else bool(same_visit))}
        return dict(c)

    def refer(self, patient_id, *, destination, day, reason):
        c = self._c(patient_id)
        if c["via_result"] != "suspicious-of-cancer":
            raise TeleReadError(
                "referral is for suspicious-of-cancer; a positive screen "
                "should be treated, not referred")
        if not reason:
            raise TeleReadError("referral requires a reason")
        c["referred"] = True
        c["referral"] = {"day": day, "reason": reason}
        return dict(c)

    def open_loops(self, as_of, days=7):
        """Positive screens with no treatment and no referral, past the
        grace window — the women most likely to be lost."""
        out = []
        for pid, c in self.cascade.items():
            if c["via_result"] in ("positive", "suspicious-of-cancer") \
                    and not c["treatment"] and not c["referred"] \
                    and as_of - c.get("result_day", c["day"]) >= days:
                out.append({"patient_id": pid, "facility": c["facility"],
                            "result": c["via_result"],
                            "days_open": as_of - c.get("result_day",
                                                       c["day"])})
        return sorted(out, key=lambda x: -x["days_open"])

    def program_stats(self):
        total = len(self.cascade)
        pos = sum(1 for c in self.cascade.values()
                  if c["via_result"] == "positive")
        treated_same = sum(1 for c in self.cascade.values()
                           if c["treatment"] and c["treatment"]["same_visit"])
        return {"screened": total, "positive": pos,
                "treated_same_visit": treated_same,
                "note": "same-visit treatment rate is THE quality "
                        "metric; denominators shown"}

    def _c(self, pid):
        if pid not in self.cascade:
            raise TeleReadError(f"unknown screening patient: {pid}")
        return self.cascade[pid]
