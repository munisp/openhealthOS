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

# ---- HPV DNA primary screening (WHO's preferred strategy): VIA/triage
# is for HPV-positives only. Recall intervals are versioned data.
HPV_RESULTS = ("negative", "positive", "invalid")
TRIAGE_METHODS = ("via", "colposcopy")
HPV_RECALL_VERSION = "hpv-recall@1.0.0"   # WHO 2021-shaped, 30-day months
# key: (situation, hiv_positive) -> months until next screen
HPV_RECALL_MONTHS = {("negative", False): 60, ("negative", True): 36,
                     ("positive-via-negative", False): 12,
                     ("positive-via-negative", True): 12}
MODALITIES = ("via", "hpv-dna")

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
               image_ref=None, hiv_positive=False, modality="via"):
        if patient_id in self.cascade:
            raise TeleReadError(f"duplicate screen: {patient_id}")
        if modality not in MODALITIES:
            raise TeleReadError(f"unknown screening modality: {modality}")
        self.cascade[patient_id] = {
            "facility": facility, "screener": screener, "day": day,
            "image_ref": image_ref, "hiv_positive": bool(hiv_positive),
            "modality": modality,
            "via_result": None, "treatment": None, "referred": False,
            "overread_case": None,
            "hpv_history": [], "hpv_result": None, "triage": None,
            "recall": None}
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
        # WHO pathway: HPV+ with a negative triage VIA is not cleared —
        # she is recalled at 12 months
        if (c["hpv_result"] == "positive" and c["triage"] == "via"
                and result == "negative"):
            c["recall"] = self._recall_plan(
                "positive-via-negative", c["hiv_positive"], day)
        return dict(c)

    # ---- HPV DNA primary pathway ----
    def _recall_plan(self, situation, hiv_positive, day):
        months = HPV_RECALL_MONTHS[(situation, bool(hiv_positive))]
        return {"situation": situation, "interval_months": months,
                "due_day": day + 30 * months,
                "recall_version": HPV_RECALL_VERSION}

    def record_hpv(self, patient_id, *, result, day):
        """The primary HPV DNA result. Append-only: a valid result
        stands; only an INVALID sample earns a retest."""
        if result not in HPV_RESULTS:
            raise TeleReadError(f"unknown HPV result: {result}")
        c = self._c(patient_id)
        if c["modality"] != "hpv-dna":
            raise TeleReadError("this patient is on the VIA pathway — "
                                "an HPV result here is a crossed wire")
        if c["hpv_result"] in ("negative", "positive"):
            raise TeleReadError(
                "a valid HPV result stands — a repeat is a new "
                "screening round, not an edit")
        c["hpv_history"].append({"result": result, "day": day})
        c["hpv_result"] = result
        c["hpv_day"] = day
        if result == "negative":
            c["recall"] = self._recall_plan("negative",
                                            c["hiv_positive"], day)
        c["awaiting_retest"] = result == "invalid"
        return dict(c)

    def triage(self, patient_id, *, method, day):
        """HPV-positive → triage decides treat-eligibility. There is no
        pathway where an HPV-positive woman is simply sent home."""
        c = self._c(patient_id)
        if c["hpv_result"] != "positive":
            raise TeleReadError("triage follows an HPV-positive result")
        if method not in TRIAGE_METHODS:
            raise TeleReadError(f"unknown triage method: {method}")
        if c["triage"]:
            raise TeleReadError("triage already recorded")
        c["triage"] = method
        c["triage_day"] = day
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
        suspicious = c["via_result"] == "suspicious-of-cancer"
        to_colpo = (c["hpv_result"] == "positive"
                    and c["triage"] == "colposcopy")
        if not (suspicious or to_colpo):
            raise TeleReadError(
                "referral is for suspicious-of-cancer or HPV+ triaged "
                "to colposcopy; a positive screen should be treated, "
                "not referred")
        if not reason:
            raise TeleReadError("referral requires a reason")
        if not destination:
            raise TeleReadError("a referral without a destination is a "
                                "letter to nowhere")
        c["referred"] = True
        c["referral"] = {"day": day, "reason": reason,
                         "destination": destination}
        return dict(c)

    def hpv_recall_due(self, as_of):
        """Women past their recall date — overdue-first. The call/recall
        list is the programme; without it screening is a one-off event."""
        out = [{"patient_id": pid, **c["recall"],
                "overdue_days": as_of - c["recall"]["due_day"]}
               for pid, c in self.cascade.items()
               if c.get("recall") and c["recall"]["due_day"] <= as_of]
        out.sort(key=lambda r: (-r["overdue_days"], r["patient_id"]))
        return out

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
            elif c["hpv_result"] == "positive" and not c["triage"] \
                    and as_of - c.get("hpv_day", c["day"]) >= days:
                out.append({"patient_id": pid, "facility": c["facility"],
                            "result": "hpv-positive-awaiting-triage",
                            "days_open": as_of - c.get("hpv_day",
                                                       c["day"])})
            elif c.get("awaiting_retest") \
                    and as_of - c.get("hpv_day", c["day"]) >= days:
                out.append({"patient_id": pid, "facility": c["facility"],
                            "result": "hpv-invalid-awaiting-retest",
                            "days_open": as_of - c.get("hpv_day",
                                                       c["day"])})
        return sorted(out, key=lambda x: -x["days_open"])

    def program_stats(self):
        total = len(self.cascade)
        pos = sum(1 for c in self.cascade.values()
                  if c["via_result"] == "positive")
        treated_same = sum(1 for c in self.cascade.values()
                           if c["treatment"] and c["treatment"]["same_visit"])
        hpv_tested = [c for c in self.cascade.values()
                      if c["hpv_result"] in ("negative", "positive")]
        return {"screened": total, "positive": pos,
                "treated_same_visit": treated_same,
                "hpv_tested": len(hpv_tested),
                "hpv_positive": sum(1 for c in hpv_tested
                                    if c["hpv_result"] == "positive"),
                "hpv_positivity_rate": (
                    round(sum(1 for c in hpv_tested
                              if c["hpv_result"] == "positive")
                          / len(hpv_tested), 3) if hpv_tested else None),
                "note": "same-visit treatment rate is THE quality "
                        "metric; denominators shown"}

    def _c(self, pid):
        if pid not in self.cascade:
            raise TeleReadError(f"unknown screening patient: {pid}")
        return self.cascade[pid]
