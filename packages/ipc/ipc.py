"""ipc.py — infection prevention & control for the hospital.

Population surveillance counts a country's cases; this package counts
the harms the hospital itself causes: central-line infections,
catheter UTIs, surgical-site infections, ventilator pneumonias — each
against its DEVICE-DAY denominator, because a rate without a
denominator is a vibe.

Doctrine:
- NO RATE WITHOUT A DENOMINATOR: a HAI rate computed over unrecorded
  device days refuses — it reports "denominator not stated", never
  zero, never a quiet estimate.
- ISOLATION IS A WARD-BOARD FACT: an active isolation flag is visible
  state; discontinuing it carries a stated reason from a named hand.
- AUDITS ARE MEASURED, NOT VIBED: hand-hygiene compliance is
  opportunities observed vs compliant — zero opportunities refuses.
- OUTBREAK THRESHOLDS ARE DECLARED: a breach is surfaced with the
  threshold it crossed, stated, not discovered by a newspaper.
"""

IPC_VERSION = "ipc@1.0.0"


class Error(Exception):
    pass


DEVICE_CLASSES = ("central-line", "urinary-catheter", "ventilator")

# device-linked HAI kinds -> the denominator they divide by
HAI_KINDS = {"clabsi": "central-line", "cauti": "urinary-catheter",
             "vap": "ventilator", "ssi": None}   # ssi: per 100 procedures

PRECAUTIONS = ("contact", "droplet", "airborne", "standard")


class IpcRegister:
    def __init__(self):
        self.device_days = {}        # (ward, device_class, day) -> n
        self.hais = []               # hospital-onset infections
        self.isolations = {}         # patient_ref -> active flag
        self.hh_audits = []          # hand-hygiene observations
        self.procedures = {}         # (ward, day) -> surgical count
        self.thresholds = {}         # (ward, organism) -> max active
        self.outbreak_signals = []

    # ---- denominators
    def record_device_days(self, ward, device_class, days, *, day,
                           by):
        if device_class not in DEVICE_CLASSES:
            raise Error(f"unknown device class {device_class!r}; "
                        f"classes are {DEVICE_CLASSES}")
        if days < 0:
            raise Error("device days are a counted fact, not a "
                        "negative")
        key = (ward, device_class, day)
        self.device_days[key] = self.device_days.get(key, 0) + days
        return {"ward": ward, "device_class": device_class,
                "day": day, "days": self.device_days[key]}

    def record_procedures(self, ward, count, *, day, by):
        if count < 0:
            raise Error("procedure counts are counted facts")
        key = (ward, day)
        self.procedures[key] = self.procedures.get(key, 0) + count

    # ---- hospital-onset infections
    def report_hai(self, patient_ref, kind, *, ward, onset_day,
                   device_linked=True, by, day):
        if kind not in HAI_KINDS:
            raise Error(f"unknown HAI kind {kind!r}; kinds are "
                        f"{sorted(HAI_KINDS)}")
        hai = {"patient_ref": patient_ref, "kind": kind,
               "ward": ward, "onset_day": onset_day,
               "device_linked": device_linked,
               "reported_by": by, "reported_day": day}
        self.hais.append(hai)
        return hai

    def _denominator(self, ward, kind, from_day, to_day):
        device = HAI_KINDS[kind]
        if device is None:    # ssi divides by procedures
            return sum(n for (w, d), n in self.procedures.items()
                       if w == ward and from_day <= d <= to_day)
        return sum(n for (w, c, d), n in self.device_days.items()
                   if w == ward and c == device
                   and from_day <= d <= to_day)

    def hai_rate(self, ward, kind, *, from_day, to_day):
        """CLABSI/CAUTI/VAP per 1,000 device-days; SSI per 100
        procedures. A missing denominator REFUSES — it does not
        report zero."""
        if kind not in HAI_KINDS:
            raise Error(f"unknown HAI kind {kind!r}")
        denom = self._denominator(ward, kind, from_day, to_day)
        if denom == 0:
            raise Error(f"denominator for {kind} in {ward} is not "
                        "stated for the window — a rate without a "
                        "denominator is refused, not zero")
        numer = sum(1 for h in self.hais
                    if h["ward"] == ward and h["kind"] == kind
                    and from_day <= h["onset_day"] <= to_day)
        per = 100 if HAI_KINDS[kind] is None else 1000
        return {"ward": ward, "kind": kind, "infections": numer,
                "denominator": denom,
                "rate": round(numer / denom * per, 2),
                "per": per, "window": [from_day, to_day]}

    # ---- isolation
    def flag_isolation(self, patient_ref, *, organism, precautions,
                       ward, by, day):
        if precautions not in PRECAUTIONS:
            raise Error(f"unknown precautions {precautions!r}; "
                        f"precautions are {PRECAUTIONS}")
        if patient_ref in self.isolations:
            raise Error(f"{patient_ref} already flagged — extend or "
                        "discontinue, never double-flag")
        flag = {"patient_ref": patient_ref, "organism": organism,
                "precautions": precautions, "ward": ward,
                "flagged_by": by, "flagged_day": day,
                "active": True}
        self.isolations[patient_ref] = flag
        self._check_outbreak(ward, organism, day)
        return flag

    def discontinue_isolation(self, patient_ref, *, reason, by, day):
        flag = self.isolations.get(patient_ref)
        if not flag or not flag["active"]:
            raise Error(f"no active isolation for {patient_ref}")
        if not reason:
            raise Error("discontinuing isolation carries a stated "
                        "reason — an invisible lift is a breach")
        flag["active"] = False
        flag["discontinued_by"] = by
        flag["discontinued_day"] = day
        flag["discontinue_reason"] = reason
        return flag

    def ward_board(self, ward):
        """Active isolations on a ward — the fact the board needs."""
        return [f for f in self.isolations.values()
                if f["active"] and f["ward"] == ward]

    # ---- hand hygiene
    def hand_hygiene_audit(self, ward, *, opportunities, compliant,
                           observer, day):
        if opportunities < 1:
            raise Error("an audit with zero opportunities observed "
                        "is not an audit — refused")
        if compliant > opportunities:
            raise Error("compliant cannot exceed opportunities — "
                        "the numbers are observed, not wished")
        audit = {"ward": ward, "opportunities": opportunities,
                 "compliant": compliant, "observer": observer,
                 "day": day,
                 "compliance": round(compliant / opportunities, 4)}
        self.hh_audits.append(audit)
        return audit

    # ---- outbreak thresholds
    def declare_threshold(self, ward, organism, *, max_active, by,
                          day):
        if max_active < 1:
            raise Error("an outbreak threshold is a declared "
                        "positive count")
        self.thresholds[(ward, organism)] = {
            "max_active": max_active, "declared_by": by,
            "declared_day": day}

    def _check_outbreak(self, ward, organism, day):
        th = self.thresholds.get((ward, organism))
        if not th:
            return
        active = sum(1 for f in self.isolations.values()
                     if f["active"] and f["ward"] == ward
                     and f["organism"] == organism)
        if active > th["max_active"]:
            self.outbreak_signals.append({
                "ward": ward, "organism": organism,
                "active": active, "threshold": th["max_active"],
                "day": day,
                "reason": f"{active} active {organism} isolations in "
                          f"{ward} cross the declared threshold "
                          f"{th['max_active']} — stated, not silent"})
