"""mammoprog.py — mammography PROGRAMME tracking, not one-off reads.

A screening mammogram is not a read; it is a node in a programme:
invite → screen → BI-RADS category → the RIGHT next step at the RIGHT
interval → outcome. The QA of a mammography programme is its audit
metrics — recall rate, cancer detection rate, PPV — and every one of
them is a rate that lives or dies by its denominator.

BI-RADS drives the pathway (versioned, screening-programme-shaped):
  0  incomplete      → additional imaging, due NOW (days)
  1  negative        → routine recall
  2  benign          → routine recall
  3  probably benign → short-interval follow-up (6 months)
  4  suspicious      → biopsy referral (14 days)
  5  highly suggestive of malignancy → biopsy referral (14 days)
  6  known malignancy → treatment pathway, not recall

Doctrine:
  - the category decides the interval — a BI-RADS 3 on a routine
    3-year recall is how probably-benign becomes diagnosed-late
  - a BI-RADS 0 with no additional imaging is an OPEN LOOP, not a
    negative screen — the programme owes her the rest of the exam
  - every worklist is overdue-first; the denominator is every woman
    ever screened, not every woman who came back
  - the audit (recall rate, detection rate per 1000, PPV of biopsy)
    shows every n — a rate without n is marketing
"""

MAMMO_PROGRAM_VERSION = "mammo-programme@1.0.0"   # NHSBSP/WHO-shaped

BIRADS = ("0", "1", "2", "3", "4", "5", "6")
BIRADS_PATHWAY = {
    "0": {"kind": "additional-imaging", "due_days": 30},
    "1": {"kind": "routine-recall", "due_days": 30 * 36},
    "2": {"kind": "routine-recall", "due_days": 30 * 36},
    "3": {"kind": "short-interval", "due_days": 30 * 6},
    "4": {"kind": "biopsy-referral", "due_days": 14},
    "5": {"kind": "biopsy-referral", "due_days": 14},
    "6": {"kind": "treatment-pathway", "due_days": 14},
}
BIOPSY_GRADES = ("4", "5")
BIOPSY_OUTCOMES = ("benign", "malignant")


class MammoError(Exception):
    pass


class MammoProgram:
    def __init__(self):
        self.screens = {}      # patient_id -> [screen records]

    def record_screen(self, patient_id, *, day, birads, by, facility,
                      image_ref):
        if birads not in BIRADS:
            raise MammoError(f"unknown BI-RADS category: {birads}")
        if not by:
            raise MammoError("an unsigned screen is no screen")
        if not image_ref:
            raise MammoError("no image reference — nothing was read")
        rec = {"patient_id": patient_id, "day": day, "birads": birads,
               "by": by, "facility": facility, "image_ref": image_ref,
               "pathway": dict(BIRADS_PATHWAY[birads],
                               due_day=day
                               + BIRADS_PATHWAY[birads]["due_days"]),
               "pathway_completed_day": None,
               "biopsy_outcome": None,
               "programme_version": MAMMO_PROGRAM_VERSION}
        self.screens.setdefault(patient_id, []).append(rec)
        return dict(rec)

    def complete_pathway(self, patient_id, *, day, what):
        """The owed step happened: additional imaging done, biopsy
        attended, recall screen recorded. 'what' must match the owed
        pathway kind — completing the wrong step is a crossed wire."""
        s = self._latest(patient_id)
        owed = s["pathway"]["kind"]
        if what != owed:
            raise MammoError(
                f"pathway owes {owed!r}, not {what!r} — completing "
                "the wrong step closes nothing")
        if s["pathway_completed_day"] is not None:
            raise MammoError("pathway step already completed — the "
                             "ledger does not double-close")
        s["pathway_completed_day"] = day
        return dict(s)

    def record_biopsy_outcome(self, patient_id, *, day, outcome):
        """Biopsy result feeds the audit. Only biopsy-grade screens
        earn outcomes — recording a biopsy result for a BI-RADS 1 is
        a crossed wire."""
        if outcome not in BIOPSY_OUTCOMES:
            raise MammoError(f"unknown biopsy outcome: {outcome}")
        s = self._latest(patient_id)
        if s["birads"] not in BIOPSY_GRADES:
            raise MammoError(
                f"BI-RADS {s['birads']} does not enter the biopsy "
                "audit — a biopsy outcome without a biopsy-grade "
                "screen is a crossed wire")
        if s["biopsy_outcome"]:
            raise MammoError("biopsy outcome recorded — the lab "
                             "notebook does not erase")
        s["biopsy_outcome"] = {"day": day, "outcome": outcome}
        return dict(s)

    # ---- worklists ----
    def due(self, as_of):
        """Every woman whose owed step is incomplete and due —
        overdue-first. BI-RADS 0s lead: an incomplete exam is a debt
        with the shortest fuse."""
        out = []
        for pid, screens in self.screens.items():
            s = screens[-1]
            if s["pathway_completed_day"] is not None:
                continue
            if s["pathway"]["due_day"] <= as_of:
                out.append({"patient_id": pid, "birads": s["birads"],
                            "owes": s["pathway"]["kind"],
                            "due_day": s["pathway"]["due_day"],
                            "overdue_days":
                                as_of - s["pathway"]["due_day"]})
        out.sort(key=lambda x: (-x["overdue_days"], x["patient_id"]))
        return out

    # ---- the audit ----
    def audit(self):
        """Programme metrics with every denominator shown:
        recall rate (BI-RADS 0 fraction — the cost of incompleteness),
        biopsy PPV (malignant / biopsied 4&5), cancer detection per
        1000 screens."""
        all_screens = [s for screens in self.screens.values()
                       for s in screens]
        n = len(all_screens)
        zeros = sum(1 for s in all_screens if s["birads"] == "0")
        biopsied = [s for s in all_screens
                    if s["birads"] in BIOPSY_GRADES
                    and s["biopsy_outcome"]]
        malignant = sum(1 for s in biopsied
                        if s["biopsy_outcome"]["outcome"]
                        == "malignant")
        return {
            "screens": n,
            "recall_rate": (round(zeros / n, 3) if n else None),
            "recall_numerator": zeros,
            "biopsies_with_outcome": len(biopsied),
            "biopsy_ppv": (round(malignant / len(biopsied), 3)
                           if biopsied else None),
            "cancers_detected": malignant,
            "detection_per_1000": (round(malignant / n * 1000, 2)
                                   if n else None),
            "programme_version": MAMMO_PROGRAM_VERSION,
            "note": "recall rate, PPV, and detection rate are the "
                    "programme's vital signs; every one shows its n"}

    def _latest(self, pid):
        if pid not in self.screens:
            raise MammoError(f"never screened in this programme: {pid}")
        return self.screens[pid][-1]
