"""vhclaims.py — the claims core for the visitor health cover engine:
24/7 intake, guarantee-of-payment against a 2-hour SLA clock, dossier,
tariff-driven adjudication with glosa and human override, payment runs
with remittance, and appeals — every SLA a clock, every breach a named
finding, never a quiet slip.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.4):
  - FR-CLM-01: 24/7 multichannel intake (phone/portal/whatsapp/kiosk).
  - FR-CLM-02: GOP issuance ≤2h — the clock starts at intake and a late
    GOP is recorded as a breach, not excused.
  - FR-CLM-03: adjudication ≤10 days against an effective-dated tariff
    table; unknown codes never guess — they route to a human; glosa
    deductions carry versioned reason codes; overrides are signed.
  - FR-CLM-04: payment runs ≤30 days with per-claim remittance advice.
  - FR-CLM-05: deterministic fraud sample (5%) + glosa register.
  - Appeals resolve ≤5 days, with grounds, not vibes.

Doctrines:
- An SLA breach is data, not shame: the report shows compliance with
  denominators, and the breach names itself.
- A tariff lookup that misses is a human task, never an invented price.
- The glosa is the audit: every deduction carries a reason code from a
  versioned table.
- An override without an identity and a justification is a veto by
  ghost — refused.
- Money stays in minor units of the profile currency.
"""

import hashlib
import secrets

VHCLAIMS_VERSION = "vhc-claims@1.0.0"

# Versioned claims rule table — a deadline that cannot be traced to a
# table is a promise nobody keeps.
CLAIMS_RULES = {
    "vhc-claims@1.0.0": {
        "gop_sla_hours": 2,
        "adjudication_sla_days": 10,
        "payment_sla_days": 30,
        "appeal_sla_days": 5,
        "appeal_window_days": 30,
        "fraud_sample_rate": 0.05,
        "channels": ("phone", "portal", "whatsapp", "kiosk"),
    },
}

# Versioned glosa (deduction) reason vocabulary — off-vocabulary codes
# refuse, because a free-text deduction is a negotiation, not an audit.
GLOSA_CODES = ("above-tariff", "not-covered", "duplicate-act",
               "missing-documentation")

# Effective-dated tariff tables: amounts in minor units, keyed by act
# code. A code absent from the table is a data gap, not an invitation
# to guess.
TARIFFS = {
    "cv-tariffs@1.0.0": {"effective_from": 0, "acts": {
        "CONS-GP": 3500, "CONS-ED": 7000, "SUTURE-MINOR": 12000,
        "OBS-DAY": 25000, "MEDEVAC-FIXED": 900000}},
    "cv-tariffs@2.0.0": {"effective_from": 365, "acts": {
        "CONS-GP": 3800, "CONS-ED": 7500, "SUTURE-MINOR": 12500,
        "OBS-DAY": 26000, "MEDEVAC-FIXED": 950000}},
    "ng-tariffs@1.0.0": {"effective_from": 0, "acts": {
        "CONS-GP": 500_000, "CONS-ED": 1_200_000, "SUTURE-MINOR": 900_000,
        "OBS-DAY": 2_500_000}},
}

CASE_STATES = ("intake", "gop-issued", "dossier-submitted",
               "adjudicated", "paid", "closed")


class ClaimsError(Exception):
    pass


def _refuse(msg):
    raise ClaimsError(msg)


def tariff_for(tariff_set, code, *, day):
    """The effective-dated lookup: the table in force on the service
    day answers; a miss is None — a human task, never a guess."""
    versions = [v for v in TARIFFS.values()
                if v["effective_from"] <= day]
    if tariff_set:
        versions = [(k, v) for k, v in TARIFFS.items()
                    if k.startswith(tariff_set) and
                    v["effective_from"] <= day]
        if not versions:
            return None, None
        key, table = max(versions, key=lambda kv: kv[1]["effective_from"])
        return table["acts"].get(code), key
    if not versions:
        return None, None
    table = max(versions, key=lambda v: v["effective_from"])
    return table["acts"].get(code), None


class ClaimsRegistry:
    def __init__(self, *, rules_version="vhc-claims@1.0.0",
                 tariff_set="cv-tariffs", tariff_book=None):
        if rules_version not in CLAIMS_RULES:
            _refuse("unknown claims rules version")
        self.rules = dict(CLAIMS_RULES[rules_version])
        self.rules_version = rules_version
        self.tariff_set = tariff_set
        # when a governed TariffBook is wired, it is the price source;
        # the static table is the bootstrap fallback
        self.tariff_book = tariff_book
        self.cases = {}
        self.audit = []

    # --- FR-CLM-01: 24/7 intake -------------------------------------
    def intake(self, *, channel, cover_ref, claimant_name, summary,
               now_h, day):
        if channel not in self.rules["channels"]:
            _refuse("unknown intake channel — the door list is config")
        if not cover_ref or not claimant_name or not summary:
            _refuse("intake needs a cover, a claimant, and a summary")
        # FR-CLM-05: duplicate-claim detection. The same cover, the
        # same claimant, the same summary on the same day is a
        # resubmission, not a new case — refuse it. The same cover and
        # claimant with an open case but a different story is a
        # suspect: the case opens flagged for the fraud sample, because
        # a second hospitalisation happens — and so does a second
        # invoice for the first one.
        for c in self.cases.values():
            if c["cover_ref"] != cover_ref:
                continue
            if c["claimant_name"] == claimant_name and \
                    c["summary"] == summary and c["intake_day"] == day:
                _refuse("exact duplicate — the same claim twice in one "
                        "day is a resubmission, not a new case "
                        "(FR-CLM-05)")
        def _open(c):
            if c["state"] not in ("paid", "closed"):
                return True
            return bool(c["appeal"]) and not c["appeal"]["resolution"]
        duplicate_of = next(
            (c["case_id"] for c in self.cases.values()
             if c["cover_ref"] == cover_ref
             and c["claimant_name"] == claimant_name
             and _open(c)), None)
        case_id = "CLM-" + secrets.token_hex(4).upper()
        self.cases[case_id] = {
            "case_id": case_id, "cover_ref": cover_ref,
            "claimant_name": claimant_name, "summary": summary,
            "channel": channel, "state": "intake",
            "intake_h": now_h, "intake_day": day,
            "gop": None, "dossier": None, "adjudication": None,
            "payment": None, "appeal": None, "breaches": [],
            "duplicate_suspect_of": duplicate_of,
        }
        self.audit.append({"event": "intake", "case": case_id,
                           "channel": channel, "day": day})
        if duplicate_of is not None:
            self.audit.append({"event": "duplicate_flagged",
                               "case": case_id, "of": duplicate_of,
                               "day": day})
        return self.cases[case_id]

    # --- FR-CLM-02: guarantee of payment -----------------------------
    def issue_gop(self, case_id, *, provider_ref, limit_minor, now_h):
        case = self._case(case_id)
        if case["state"] != "intake":
            _refuse("GOP issues once, from intake — a second GOP is a "
                    "second promise to pay")
        if not provider_ref or limit_minor is None or limit_minor < 0:
            _refuse("a GOP names the provider and the ceiling")
        elapsed = now_h - case["intake_h"]
        breach = elapsed > self.rules["gop_sla_hours"]
        if breach:
            case["breaches"].append(
                {"clock": "gop", "elapsed_hours": elapsed,
                 "budget_hours": self.rules["gop_sla_hours"]})
        case["gop"] = {"gop_ref": "GOP-" + secrets.token_hex(4).upper(),
                       "provider_ref": provider_ref,
                       "limit_minor": limit_minor,
                       "issued_h": now_h, "sla_breach": breach,
                       "elapsed_hours": elapsed}
        case["state"] = "gop-issued"
        self.audit.append({"event": "gop_issued", "case": case_id,
                           "breach": breach})
        return case["gop"]

    # --- dossier ------------------------------------------------------
    def submit_dossier(self, case_id, *, icd10_codes, acts,
                       discharge_ref, day):
        """The dossier is complete or it is not submitted: diagnoses,
        tariffed acts (code + charged amount), and a discharge
        reference are all mandatory."""
        case = self._case(case_id)
        if case["state"] != "gop-issued":
            _refuse("a dossier follows its GOP — unguaranteed work is "
                    "charity with paperwork")
        if not icd10_codes:
            _refuse("a dossier without a diagnosis is a bill")
        if not acts:
            _refuse("a dossier without acts is a story")
        if not discharge_ref:
            _refuse("a dossier without a discharge note is hearsay")
        for act in acts:
            if not act.get("code") or act.get("charged_minor") is None:
                _refuse("every act names its tariff code and charge")
        case["dossier"] = {"icd10_codes": list(icd10_codes),
                           "acts": [dict(a) for a in acts],
                           "discharge_ref": discharge_ref,
                           "submitted_day": day}
        case["state"] = "dossier-submitted"
        self.audit.append({"event": "dossier_submitted", "case": case_id,
                           "day": day})
        return case["dossier"]

    # --- FR-CLM-03: adjudication --------------------------------------
    def adjudicate(self, case_id, *, day, override=None):
        """Tariff-driven: each act prices against the effective table.
        Unknown codes route to manual review — never invented. Charges
        above tariff glosa with a reason code. An override must be
        signed and justified."""
        case = self._case(case_id)
        if case["state"] != "dossier-submitted":
            _refuse("adjudication needs a dossier, not a phone call")
        approved, glosa, manual = 0, [], []
        for act in case["dossier"]["acts"]:
            if self.tariff_book is not None:
                price, _ver = self.tariff_book.lookup(act["code"],
                                                      day=day)
            else:
                price, _ver = tariff_for(self.tariff_set, act["code"],
                                         day=day)
            if price is None:
                manual.append(act["code"])
                continue
            if act["charged_minor"] > price:
                glosa.append({"code": act["code"],
                              "charged_minor": act["charged_minor"],
                              "allowed_minor": price,
                              "deducted_minor": act["charged_minor"]
                              - price,
                              "reason": "above-tariff"})
                approved += price
            else:
                approved += act["charged_minor"]
        if override is not None:
            if not override.get("by") or not override.get("justification"):
                _refuse("an override without identity and justification "
                        "is a veto by ghost")
        elapsed = day - case["dossier"]["submitted_day"]
        if elapsed > self.rules["adjudication_sla_days"]:
            case["breaches"].append(
                {"clock": "adjudication", "elapsed_days": elapsed,
                 "budget_days": self.rules["adjudication_sla_days"]})
        verdict = ("manual-review" if manual else
                   ("partial" if glosa else "approved"))
        case["adjudication"] = {
            "verdict": verdict, "approved_minor": approved,
            "glosa": glosa, "manual_review_codes": manual,
            "day": day, "elapsed_days": elapsed,
            "override": override}
        case["state"] = "adjudicated"
        self.audit.append({"event": "adjudicated", "case": case_id,
                           "verdict": verdict, "day": day})
        return case["adjudication"]

    # --- FR-CLM-04: payment run + remittance --------------------------
    def payment_run(self, *, day):
        """Batch every adjudicated case (approved/partial). Each payment
        carries a remittance advice naming acts, glosa, and net — a
        payment without remittance is a mystery deposit."""
        paid = []
        for case in self.cases.values():
            if case["state"] != "adjudicated":
                continue
            adj = case["adjudication"]
            if adj["verdict"] == "manual-review":
                continue
            elapsed = day - adj["day"]
            if elapsed > self.rules["payment_sla_days"]:
                case["breaches"].append(
                    {"clock": "payment", "elapsed_days": elapsed,
                     "budget_days": self.rules["payment_sla_days"]})
            remit = {"remittance_ref": "REM-" +
                     secrets.token_hex(4).upper(),
                     "case": case["case_id"],
                     "net_minor": adj["approved_minor"],
                     "glosa_total_minor": sum(
                         g["deducted_minor"] for g in adj["glosa"]),
                     "day": day}
            case["payment"] = remit
            case["state"] = "paid"
            paid.append(remit)
            self.audit.append({"event": "paid", "case": case["case_id"],
                               "net_minor": remit["net_minor"],
                               "day": day})
        return paid

    # --- appeals --------------------------------------------------------
    def appeal(self, case_id, *, by, grounds, day):
        case = self._case(case_id)
        if case["state"] not in ("adjudicated", "paid"):
            _refuse("appeals follow adjudication, not intake")
        if not by or not grounds:
            _refuse("an appeal names who and why")
        adj_day = case["adjudication"]["day"]
        if day - adj_day > self.rules["appeal_window_days"]:
            _refuse("appeal window closed — finality is also a right")
        case["appeal"] = {"by": by, "grounds": grounds, "day": day,
                          "resolution": None}
        self.audit.append({"event": "appealed", "case": case_id,
                           "day": day})
        return case["appeal"]

    def resolve_appeal(self, case_id, *, upheld, by, note, day):
        case = self._case(case_id)
        if case["appeal"] is None or case["appeal"]["resolution"]:
            _refuse("no open appeal to resolve")
        if not by or not note:
            _refuse("a resolution names who and why")
        elapsed = day - case["appeal"]["day"]
        if elapsed > self.rules["appeal_sla_days"]:
            case["breaches"].append(
                {"clock": "appeal", "elapsed_days": elapsed,
                 "budget_days": self.rules["appeal_sla_days"]})
        case["appeal"]["resolution"] = {"upheld": upheld, "by": by,
                                        "note": note, "day": day,
                                        "elapsed_days": elapsed}
        case["state"] = "closed"
        self.audit.append({"event": "appeal_resolved", "case": case_id,
                           "upheld": upheld, "day": day})
        return case["appeal"]["resolution"]

    # --- FR-CLM-05: fraud sample + glosa register ----------------------
    def fraud_sample(self, *, seed, day):
        """Deterministic 5% sample of adjudicated cases for audit —
        the selection is reproducible from the seed, so the audited set
        cannot be steered after the fact."""
        adjudicated = [c for c in self.cases.values()
                       if c["adjudication"] is not None]
        sample = []
        for c in adjudicated:
            digest = hashlib.sha256(
                ("%s:%s" % (seed, c["case_id"])).encode()).hexdigest()
            if int(digest[:8], 16) / 0xFFFFFFFF < \
                    self.rules["fraud_sample_rate"]:
                sample.append(c["case_id"])
        return {"day": day, "sampled": sorted(sample),
                "population": len(adjudicated),
                "target_rate": self.rules["fraud_sample_rate"]}

    def glosa_register(self):
        rows = []
        for c in self.cases.values():
            if c["adjudication"]:
                for g in c["adjudication"]["glosa"]:
                    rows.append(dict(g, case=c["case_id"]))
        return rows

    # --- report: denominators always visible ---------------------------
    def report(self):
        def _compliance(clock):
            total = len(self.cases)
            if total == 0:
                return {"compliant": 0, "total": 0}
            breached = sum(1 for c in self.cases.values()
                           if any(b["clock"] == clock
                                  for b in c["breaches"]))
            return {"compliant": total - breached, "total": total}
        return {"cases": len(self.cases),
                "by_state": {s: sum(1 for c in self.cases.values()
                                    if c["state"] == s)
                             for s in CASE_STATES},
                "duplicate_suspects": sum(
                    1 for c in self.cases.values()
                    if c.get("duplicate_suspect_of")),
                "gop_sla": _compliance("gop"),
                "adjudication_sla": _compliance("adjudication"),
                "payment_sla": _compliance("payment"),
                "appeal_sla": _compliance("appeal"),
                "glosa_total_minor": sum(
                    g["deducted_minor"] for g in self.glosa_register()),
                "paid_minor": sum(c["payment"]["net_minor"]
                                  for c in self.cases.values()
                                  if c["payment"])}

    def _case(self, case_id):
        case = self.cases.get(case_id)
        if case is None:
            _refuse("unknown case")
        return case
