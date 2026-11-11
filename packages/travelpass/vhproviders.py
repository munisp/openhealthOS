"""vhproviders.py — the provider side of the scheme: accreditation
workflow, effective-dated tariff administration under four-eyes, the
provider portal surface, and the weekly MoH pack.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.5):
  - FR-PRV-01: provider portal — dossier submission against the
    provider's own GOPs, case visibility scoped to the provider.
  - FR-PRV-02: effective-dated tariff schedule — proposals are
    versioned, activation needs a second pair of eyes, old versions
    are immutable history.
  - FR-PRV-03: accreditation workflow — licence, verification checks,
    accreditation, suspension and revocation all name who and why.
  - Weekly MoH pack: the scheme's health, with denominators.

Doctrines:
- Four-eyes: a tariff change proposed and approved by the same hand
  is a price set in private — refused.
- A provider bills its own guarantees. Billing against someone else's
  GOP is identity theft with an invoice number.
- Unaccredited providers don't submit dossiers; the door is the
  accreditation ledger, not a promise.
- The weekly pack shows denominators; a percentage without a base is
  a slogan.
"""

import secrets

VHPROVIDERS_VERSION = "vhc-providers@1.0.0"

PROVIDER_STATES = ("applicant", "verified", "accredited", "suspended",
                   "revoked")


class ProviderError(Exception):
    pass


def _refuse(msg):
    raise ProviderError(msg)


class TariffBook:
    """Effective-dated tariff administration. Proposals are versioned
    and immutable once approved; activation is a four-eyes act."""

    def __init__(self, *, book_id):
        self.book_id = book_id
        self.versions = {}   # version -> {acts, effective_from, ...}
        self.audit = []

    def propose(self, *, version, acts, effective_from, by, day):
        if not version or version in self.versions:
            _refuse("a tariff version is named once — reuse rewrites "
                    "history")
        if not acts:
            _refuse("an empty tariff table is not a proposal")
        for code, price in acts.items():
            if not code or price is None or price < 0:
                _refuse("every act names a code and a non-negative "
                        "price")
        if not by:
            _refuse("a proposal names its proposer")
        self.versions[version] = {
            "version": version, "acts": dict(acts),
            "effective_from": effective_from, "proposed_by": by,
            "proposed_day": day, "approved_by": None,
            "status": "proposed"}
        self.audit.append({"event": "tariff_proposed",
                           "version": version, "by": by, "day": day})
        return self.versions[version]

    def approve(self, version, *, by, day):
        v = self._version(version)
        if v["status"] != "proposed":
            _refuse("only a proposal can be approved — an approved "
                    "table is immutable history")
        if by == v["proposed_by"]:
            _refuse("four-eyes: the hand that proposes never approves")
        if not by:
            _refuse("an approval names its approver")
        v["approved_by"] = by
        v["approved_day"] = day
        v["status"] = "approved"
        self.audit.append({"event": "tariff_approved",
                           "version": version, "by": by, "day": day})
        return v

    def lookup(self, code, *, day):
        """The table in force on the service day answers; a miss is
        None — a human task, never a guess."""
        candidates = [v for v in self.versions.values()
                      if v["status"] == "approved"
                      and v["effective_from"] <= day]
        if not candidates:
            return None, None
        table = max(candidates, key=lambda v: v["effective_from"])
        return table["acts"].get(code), table["version"]

    def in_force(self, *, day):
        candidates = [v for v in self.versions.values()
                      if v["status"] == "approved"
                      and v["effective_from"] <= day]
        if not candidates:
            return None
        return max(candidates, key=lambda v: v["effective_from"])

    def _version(self, version):
        v = self.versions.get(version)
        if v is None:
            _refuse("unknown tariff version")
        return v


class ProviderRegistry:
    """Accreditation workflow: apply → verify → accredit, then
    suspend/revoke with reasons. Every step names who."""

    def __init__(self):
        self.providers = {}
        self.audit = []

    def apply(self, provider_id, *, legal_name, licence_ref, services,
              day):
        if not provider_id or not legal_name or not licence_ref:
            _refuse("an application names the provider, its legal "
                    "name, and its licence — anonymous care is not a "
                    "provider, it is a risk")
        if not services:
            _refuse("a provider without services is a signboard")
        if provider_id in self.providers:
            _refuse("duplicate provider id")
        self.providers[provider_id] = {
            "provider_id": provider_id, "legal_name": legal_name,
            "licence_ref": licence_ref, "services": list(services),
            "state": "applicant", "applied_day": day,
            "verified_by": None, "accredited_by": None,
            "history": [{"state": "applicant", "day": day}]}
        self.audit.append({"event": "provider_applied",
                           "provider": provider_id, "day": day})
        return self.providers[provider_id]

    def verify(self, provider_id, *, by, checks, day):
        p = self._p(provider_id)
        if p["state"] != "applicant":
            _refuse("verification follows application")
        if not by or not checks:
            _refuse("verification names the verifier and the checks "
                    "performed — 'looked fine' is not a check")
        p["verified_by"] = by
        p["state"] = "verified"
        p["history"].append({"state": "verified", "day": day,
                             "by": by, "checks": list(checks)})
        self.audit.append({"event": "provider_verified",
                           "provider": provider_id, "by": by, "day": day})
        return p

    def accredit(self, provider_id, *, by, day):
        p = self._p(provider_id)
        if p["state"] != "verified":
            _refuse("accreditation follows verification — never leap "
                    "from applicant to accredited")
        if by == p["verified_by"]:
            _refuse("four-eyes: the verifier never accredits")
        if not by:
            _refuse("accreditation names its approver")
        p["accredited_by"] = by
        p["state"] = "accredited"
        p["history"].append({"state": "accredited", "day": day,
                             "by": by})
        self.audit.append({"event": "provider_accredited",
                           "provider": provider_id, "by": by, "day": day})
        return p

    def suspend(self, provider_id, *, reason, day):
        p = self._p(provider_id)
        if p["state"] != "accredited":
            _refuse("only the accredited can be suspended")
        if not reason:
            _refuse("a suspension without a reason is a vendetta")
        p["state"] = "suspended"
        p["history"].append({"state": "suspended", "day": day,
                             "reason": reason})
        self.audit.append({"event": "provider_suspended",
                           "provider": provider_id, "day": day})
        return p

    def revoke(self, provider_id, *, reason, day):
        p = self._p(provider_id)
        if p["state"] in ("applicant", "revoked"):
            _refuse("revocation serves the accredited or suspended")
        if not reason:
            _refuse("a revocation without a reason is a disappearance")
        p["state"] = "revoked"
        p["history"].append({"state": "revoked", "day": day,
                             "reason": reason})
        self.audit.append({"event": "provider_revoked",
                           "provider": provider_id, "day": day})
        return p

    def is_active(self, provider_id):
        p = self.providers.get(provider_id)
        return p is not None and p["state"] == "accredited"

    def report(self):
        return {"providers": len(self.providers),
                "by_state": {s: sum(1 for p in self.providers.values()
                                    if p["state"] == s)
                             for s in PROVIDER_STATES}}

    def _p(self, provider_id):
        p = self.providers.get(provider_id)
        if p is None:
            _refuse("unknown provider")
        return p


class ProviderPortal:
    """FR-PRV-01: the provider-facing surface. A provider sees and
    touches only its own guarantees; everyone else's cases are a
    count, never a list."""

    def __init__(self, *, claims, providers, id_fn=None):
        self.claims = claims
        self.providers = providers
        # IDs stay secrets-unique in production; demos and
        # reproducibility harnesses inject a deterministic id_fn
        # (ADR-0079 — the checksum is the contract, so a demo must
        # be able to walk the REAL flow and still be byte-stable).
        self._id_fn = id_fn or (lambda: secrets.token_hex(4))

    def submit_dossier(self, provider_id, case_id, *, icd10_codes, acts,
                       discharge_ref, day):
        if not self.providers.is_active(provider_id):
            _refuse("the door is the accreditation ledger — "
                    "unaccredited providers don't submit")
        case = self.claims._case(case_id)
        gop = case.get("gop")
        if gop is None or gop["provider_ref"] != provider_id:
            _refuse("a provider bills its own guarantees — billing "
                    "against another's GOP is identity theft with an "
                    "invoice number")
        return self.claims.submit_dossier(
            case_id, icd10_codes=icd10_codes, acts=acts,
            discharge_ref=discharge_ref, day=day)

    def my_cases(self, provider_id):
        """Scoped visibility: own cases in detail, others as a count."""
        mine, others = [], 0
        for c in self.claims.cases.values():
            if c.get("gop") and c["gop"]["provider_ref"] == provider_id:
                mine.append({"case_id": c["case_id"],
                             "state": c["state"],
                             "approved_minor": (c["adjudication"]
                                                ["approved_minor"]
                                                if c["adjudication"]
                                                else None)})
            else:
                others += 1
        return {"mine": mine, "others_count": others,
                "note": ("other providers' cases exist but are not "
                         "yours to see")}

    def weekly_moh_pack(self, *, day, tariff_book=None):
        """FR-PRV-03: the weekly pack to the ministry — the scheme's
        health with denominators, never slogans."""
        claims_rep = self.claims.report()
        providers_rep = self.providers.report()
        pack = {
            "week_ending": day,
            "claims": claims_rep,
            "providers": providers_rep,
            "glosa_register_rows": len(self.claims.glosa_register()),
            "pack_ref": "MOH-" + self._id_fn().upper(),
        }
        if tariff_book is not None:
            force = tariff_book.in_force(day=day)
            pack["tariff_in_force"] = (force["version"]
                                       if force else "none — a stated "
                                       "gap, not an implied table")
        return pack
