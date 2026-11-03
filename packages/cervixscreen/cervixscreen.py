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

# ---- colposcopy + histology confirmation loop ----
# The biopsy that never comes back is how screening programmes lose
# women at the last step. The loop stays open — and visible — until
# histology lands.
COLPO_IMPRESSIONS = ("normal", "low-grade", "high-grade",
                     "suspicious-of-cancer")
COLPO_DECISIONS = ("discharge", "treat", "biopsy")
HISTOLOGY_RESULTS = ("normal", "cin1", "cin2", "cin3",
                     "invasive-cancer")
HISTOLOGY_VERSION = "cin-histology@1.0.0"
# impression → decisions it permits: treating a normal colpo is harm;
# treating a cancer-suspect on sight, without tissue, is worse
COLPO_RULES = {"normal": ("discharge",),
               "low-grade": ("discharge", "treat"),
               "high-grade": ("treat", "biopsy"),
               "suspicious-of-cancer": ("biopsy",)}
# histology → what MUST happen next
HISTOLOGY_MANAGEMENT = {"normal": "routine-recall",
                        "cin1": "recall-12m",
                        "cin2": "treat", "cin3": "treat",
                        "invasive-cancer": "refer-oncology"}

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
        self.device_qa = None  # attach DeviceQA to gate device methods

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
            "recall": None, "colpo": None, "specimen_id": None,
            "histology": None, "histology_pending": False,
            "oncology_referred": False}
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

    def treat(self, patient_id, *, method, day, same_visit=None,
              device_id=None):
        c = self._c(patient_id)
        via_pos = c["via_result"] == "positive"
        colpo_ok = bool(c["colpo"]) and c["colpo"]["decision"] == "treat"
        histo_ok = bool(c["histology"]) and \
            c["histology"]["result"] in ("cin2", "cin3")
        if not (via_pos or colpo_ok or histo_ok):
            raise TeleReadError(
                "treatment requires a positive VIA, a colposcopy treat "
                "decision, or CIN2/3 histology — treating without "
                "evidence is harm, not care")
        if method not in TREATMENTS:
            raise TeleReadError(f"unknown treatment: {method}")
        if method in DEVICE_TREATMENTS:
            if self.device_qa is None:
                raise TeleReadError(
                    f"{method} is a device treatment and no device QA "
                    "book is attached — a device without a QA ledger is "
                    "untested equipment, whatever the label says")
            if not device_id:
                raise TeleReadError(
                    f"{method} needs the device id — which probe "
                    "touched this patient is not a detail")
            if not self.device_qa.serviceable_on(device_id, day):
                raise TeleReadError(
                    f"{device_id} has no same-day QA pass — the probe "
                    "that was not checked today is not a treatment "
                    "device")
        ref_day = c.get("result_day")
        if not via_pos:
            ref_day = (c["colpo"]["day"] if colpo_ok
                       else c["histology"]["day"])
        c["treatment"] = {"method": method, "day": day,
                          "device_id": device_id,
                          "same_visit": (day == ref_day
                                         if same_visit is None
                                         else bool(same_visit))}
        return dict(c)

    def refer(self, patient_id, *, destination, day, reason):
        c = self._c(patient_id)
        suspicious = c["via_result"] == "suspicious-of-cancer"
        to_colpo = (c["hpv_result"] == "positive"
                    and c["triage"] == "colposcopy")
        histo_cancer = bool(c["histology"]) and \
            c["histology"]["result"] == "invasive-cancer"
        if not (suspicious or to_colpo or histo_cancer):
            raise TeleReadError(
                "referral is for suspicious-of-cancer, HPV+ triaged "
                "to colposcopy, or histology-confirmed cancer; a "
                "positive screen should be treated, not referred")
        if not reason:
            raise TeleReadError("referral requires a reason")
        if not destination:
            raise TeleReadError("a referral without a destination is a "
                                "letter to nowhere")
        c["referred"] = True
        if histo_cancer:
            c["oncology_referred"] = True
        c["referral"] = {"day": day, "reason": reason,
                         "destination": destination}
        return dict(c)

    # ---- colposcopy + histology loop ----
    def record_colposcopy(self, patient_id, *, impression, decision,
                          day, by):
        """The colposcopy exam. Gates: she must have been REFERRED
        (a colposcopy with no referral is off-pathway); the decision
        must fit the impression — treating a normal colpo is harm,
        and treating a cancer-suspect on sight, without tissue, is
        worse. Append-only: a repeat exam is a new episode, not an
        edit."""
        c = self._c(patient_id)
        if not c["referred"]:
            raise TeleReadError(
                "colposcopy follows a referral — an unreferred exam "
                "is off-pathway and unaccountable")
        if not by:
            raise TeleReadError("unsigned colposcopy is no colposcopy")
        if c["colpo"]:
            raise TeleReadError("colposcopy already recorded — a repeat "
                                "exam is a new episode, not an edit")
        if impression not in COLPO_IMPRESSIONS:
            raise TeleReadError(f"unknown colposcopy impression: "
                                f"{impression}")
        if decision not in COLPO_DECISIONS:
            raise TeleReadError(f"unknown colposcopy decision: "
                                f"{decision}")
        if decision not in COLPO_RULES[impression]:
            raise TeleReadError(
                f"impression {impression!r} does not permit decision "
                f"{decision!r} ({COLPO_RULES[impression]}) — treating "
                "a normal colpo is harm; treating a cancer-suspect "
                "without tissue is worse")
        c["colpo"] = {"impression": impression, "decision": decision,
                      "day": day, "by": by}
        if decision == "biopsy":
            c["histology_pending"] = True   # the loop OPENS, loudly
        return dict(c)

    def attach_specimen(self, patient_id, *, specimen_id):
        """Custody of the biopsy begins (registered via the crosslink
        seam into telepath). One specimen per loop."""
        c = self._c(patient_id)
        if not c["histology_pending"]:
            raise TeleReadError("no biopsy pending — a specimen without "
                                "a decision is a crossed wire")
        if c["specimen_id"]:
            raise TeleReadError("specimen already attached")
        c["specimen_id"] = specimen_id
        return dict(c)

    def record_histology(self, patient_id, *, result, day):
        """Histology CLOSES the loop the biopsy opened. Append-only:
        a report stands; a corrected report is an addendum, not an
        edit."""
        if result not in HISTOLOGY_RESULTS:
            raise TeleReadError(f"unknown histology result: {result}")
        c = self._c(patient_id)
        if not c["histology_pending"]:
            raise TeleReadError("no biopsy pending — histology without "
                                "a specimen is a crossed wire")
        if c["histology"]:
            raise TeleReadError("histology already recorded — the lab "
                                "notebook does not erase")
        if not c["specimen_id"]:
            raise TeleReadError("no specimen attached — a result with "
                                "no custody chain is uninterpretable")
        c["histology"] = {"result": result, "day": day,
                          "management": HISTOLOGY_MANAGEMENT[result],
                          "histology_version": HISTOLOGY_VERSION}
        c["histology_pending"] = False      # the loop CLOSES
        if result == "cin1":
            c["recall"] = self._recall_plan("positive-via-negative",
                                            c["hiv_positive"], day)
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
            # ---- the colposcopy/histology loops: mutually exclusive
            # with each other, so independent checks ----
            if c["histology_pending"] \
                    and as_of - c["colpo"]["day"] >= days:
                out.append({"patient_id": pid, "facility": c["facility"],
                            "result": "histology-pending",
                            "days_open": as_of - c["colpo"]["day"]})
            elif c["histology"] \
                    and c["histology"]["result"] in ("cin2", "cin3") \
                    and not c["treatment"] \
                    and as_of - c["histology"]["day"] >= days:
                out.append({"patient_id": pid, "facility": c["facility"],
                            "result": "cin-awaiting-treatment",
                            "days_open": as_of - c["histology"]["day"]})
            elif c["histology"] \
                    and c["histology"]["result"] == "invasive-cancer" \
                    and not c["oncology_referred"] \
                    and as_of - c["histology"]["day"] >= days:
                out.append({"patient_id": pid, "facility": c["facility"],
                            "result": "cancer-awaiting-oncology-referral",
                            "days_open": as_of - c["histology"]["day"]})
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


# ----------------------------------------------------------------
# Treatment-device QA: the probe that was not checked today is not a
# treatment device, it is a room heater with ambitions.
DEVICE_TREATMENTS = ("thermal-ablation", "cryotherapy")
DEVICE_QA_CHECKS = {
    "thermal-ablation": ("temp-probe-calibrated", "tip-integrity",
                         "timer-functional"),
    "cryotherapy": ("gas-pressure-ok", "hose-integrity",
                    "timer-functional"),
}
DEVICEQA_VERSION = "treat-device-qa@1.0.0"


class DeviceQA:
    """Daily QA book for treatment devices. Every check accounted for:
    'did not look' is not a pass. A device with no same-day pass is
    unserviceable — quietly using it anyway is exactly the failure
    this book exists to make loud."""

    def __init__(self):
        self.devices = {}   # device_id -> record
        self.qa_log = []    # append-only

    def register_device(self, device_id, *, kind, facility):
        if kind not in DEVICE_QA_CHECKS:
            raise TeleReadError(f"unknown device kind: {kind} — the QA "
                                "checklist is versioned data, and this "
                                "device is not in it")
        if device_id in self.devices:
            raise TeleReadError(f"duplicate device: {device_id}")
        self.devices[device_id] = {"device_id": device_id, "kind": kind,
                                   "facility": facility,
                                   "out_of_service": False,
                                   "service_note": None}
        return dict(self.devices[device_id])

    def record_qa(self, device_id, *, checks, by, day):
        d = self._d(device_id)
        if d["out_of_service"]:
            raise TeleReadError(f"{device_id} is out of service — QA "
                                "does not resurrect it; biomed "
                                "clearance does")
        if not by:
            raise TeleReadError("unsigned QA is no QA")
        required = DEVICE_QA_CHECKS[d["kind"]]
        missing = [c for c in required if c not in checks]
        failed = [c for c in required if checks.get(c) is False]
        verdict = "pass" if not missing and not failed else "fail"
        entry = {"device_id": device_id, "day": day, "by": by,
                 "checks": dict(checks), "missing": missing,
                 "failed": failed, "verdict": verdict,
                 "version": DEVICEQA_VERSION}
        self.qa_log.append(entry)
        return dict(entry)

    def mark_out_of_service(self, device_id, *, reason, by, day):
        d = self._d(device_id)
        if not reason:
            raise TeleReadError("taking a device out of service without "
                                "a reason is how it quietly comes back")
        d["out_of_service"] = True
        d["service_note"] = {"reason": reason, "by": by, "day": day}
        return dict(d)

    def return_to_service(self, device_id, *, biomed_ref, by, day):
        d = self._d(device_id)
        if not biomed_ref:
            raise TeleReadError("return to service needs a biomed "
                                "clearance reference — 'looks fine now' "
                                "is not a clearance")
        d["out_of_service"] = False
        d["service_note"] = {"biomed_ref": biomed_ref, "by": by,
                             "day": day}
        return dict(d)

    def serviceable_on(self, device_id, day):
        d = self._d(device_id)
        if d["out_of_service"]:
            return False
        return any(e["device_id"] == device_id and e["day"] == day
                   and e["verdict"] == "pass" for e in self.qa_log)

    def qa_due(self, device_ids, day):
        """Devices in use today with no pass today — overdue-first is
        meaningless for a same-day check; the list is the alarm."""
        return sorted(d for d in device_ids
                      if not self.serviceable_on(d, day))

    def _d(self, device_id):
        if device_id not in self.devices:
            raise TeleReadError(f"unknown device: {device_id}")
        return self.devices[device_id]
