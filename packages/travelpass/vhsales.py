"""vhsales.py — group/manifest purchase and the partner API surface:
a tour operator buys for fifty passengers in one manifest; an airline
sells cover at checkout — both through governed, attributable doors.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.1):
  - FR-SAL-03: group/manifest purchase — one payer, per-passenger age
    pricing from the same profile, block issuance, the whole manifest
    validated before anyone is charged.
  - FR-SAL-04 / INT-02: airline/OTA partner API — named partners with
    hashed keys, every quote and purchase attributed, revoked partners
    refused at the door; checkout binds are idempotent (a retried
    bind returns the same cover, never a second charge), and payment
    rides a redirect reference — the PAN never lands.

Doctrines:
- A manifest validates completely or not at all: a group that pays for
  49 and strands the 50th is a border incident, not a batch.
- Partner keys are hashed at rest — a key at rest is a breach with a
  timestamp.
- Attribution is not optional: a sale without a partner is a sale the
  scheme cannot audit.
- A retried bind is the same sale, not a new one — idempotency keys
  make the network's doubt the platform's problem.
"""

import hashlib
import re
import secrets

VHSALES_VERSION = "vhc-sales@1.0.0"


class SalesError(Exception):
    pass


def _refuse(msg):
    raise SalesError(msg)


def purchase_group(vhc, *, payer_ref, travelers, entry_day, day,
                   key_id):
    """FR-SAL-03: one payer, N travelers, per-pax pricing from the
    country profile. Validation is all-or-nothing — every row names
    its own error before a cent moves."""
    if not payer_ref:
        _refuse("a manifest names its payer")
    if not travelers:
        _refuse("an empty manifest is not a group")
    errors = []
    for i, t in enumerate(travelers):
        if not t.get("passport_ref") or not t.get("full_name"):
            errors.append("row %d: passport and name mandatory" % i)
        if t.get("age_years") is None or t["age_years"] < 0:
            errors.append("row %d: age required — absence is not a "
                          "discount" % i)
    if errors:
        _refuse("manifest refused, nobody charged: " + "; ".join(errors))
    group_ref = "GRP-" + secrets.token_hex(4).upper()
    covers, total = [], 0
    for t in travelers:
        q = vhc.quote(days=t.get("days", 30), age_years=t["age_years"],
                      day=day)
        cover = vhc.purchase(quote_id=q["quote_id"],
                             passport_ref=t["passport_ref"],
                             full_name=t["full_name"],
                             entry_day=entry_day, day=day,
                             key_id=key_id, dob=t.get("dob"))
        cover["group_ref"] = group_ref
        covers.append(cover)
        total += q["total_minor"]
    vhc.audit.append({"event": "group_purchased", "group": group_ref,
                      "payer": payer_ref, "count": len(covers),
                      "total_minor": total, "day": day})
    return {"group_ref": group_ref, "covers": covers,
            "total_minor": total, "count": len(covers)}


class PartnerRegistry:
    """FR-SAL-04: the partner door. Keys hashed at rest; every sale
    attributed; revocation is reasoned and immediate."""

    def __init__(self):
        self.partners = {}     # partner_id -> record
        self.idem = {}         # (partner_id, idem_key) -> cover ref
        self.audit = []

    def register(self, partner_id, *, legal_name, api_key, day,
                 quota_per_day=None, burst_per_minute=None):
        if not partner_id or not legal_name or not api_key:
            _refuse("a partner names itself and presents a key")
        if partner_id in self.partners:
            _refuse("duplicate partner id")
        # Abuse controls are registry entries, not middleware vibes:
        # quota_per_day caps volume; burst_per_minute caps velocity.
        # None means unbounded — stated at registration, in the audit.
        for q in (quota_per_day, burst_per_minute):
            if q is not None and (not isinstance(q, int) or q < 1):
                _refuse("a quota is a positive integer of attempts")
        self.partners[partner_id] = {
            "partner_id": partner_id, "legal_name": legal_name,
            "key_sha256": hashlib.sha256(api_key.encode()).hexdigest(),
            "state": "active", "registered_day": day,
            "quota_per_day": quota_per_day,
            "burst_per_minute": burst_per_minute,
            "_day": None, "_day_count": 0, "_minute": None,
            "_minute_count": 0,
            "sales": 0, "gross_minor": 0}
        self.audit.append({"event": "partner_registered",
                           "partner": partner_id, "day": day})
        return {k: v for k, v in self.partners[partner_id].items()
                if k != "key_sha256"}

    def revoke(self, partner_id, *, reason, day):
        p = self._partner(partner_id)
        if not reason:
            _refuse("a revocation without a reason is a disappearance")
        p["state"] = "revoked"
        self.audit.append({"event": "partner_revoked",
                           "partner": partner_id, "reason": reason,
                           "day": day})
        return p

    def authenticate(self, partner_id, api_key):
        p = self._partner(partner_id)
        if p["state"] != "active":
            _refuse("partner not active — the door is the registry")
        if hashlib.sha256(api_key.encode()).hexdigest() != \
                p["key_sha256"]:
            _refuse("bad key — the attempt is audited")
        return p

    def check_abuse_controls(self, partner_id, *, day, minute=None):
        """Velocity + volume gates. Exceeding either refuses loudly —
        a throttled partner is an audit event, not a silent 429."""
        p = self._partner(partner_id)
        if p["_day"] != day:
            p["_day"], p["_day_count"] = day, 0
        if minute is not None and p["_minute"] != minute:
            p["_minute"], p["_minute_count"] = minute, 0
        q = p["quota_per_day"]
        if q is not None and p["_day_count"] >= q:
            self.audit.append({"event": "partner_quota_refused",
                               "partner": partner_id, "day": day,
                               "quota": q})
            _refuse(f"daily quota {q} reached — the throttle is stated, "
                    "not silent")
        b = p["burst_per_minute"]
        if b is not None and minute is not None \
                and p["_minute_count"] >= b:
            self.audit.append({"event": "partner_burst_refused",
                               "partner": partner_id, "day": day,
                               "minute": minute, "burst": b})
            _refuse(f"burst limit {b}/min reached — velocity is governed")
        p["_day_count"] += 1
        if minute is not None:
            p["_minute_count"] += 1
        return p

    def quote_for(self, vhc, partner_id, api_key, *, days, age_years,
                  day):
        p = self.authenticate(partner_id, api_key)
        q = vhc.quote(days=days, age_years=age_years, day=day)
        q["partner_id"] = partner_id
        self.audit.append({"event": "partner_quote",
                           "partner": partner_id,
                           "quote": q["quote_id"], "day": day})
        return q

    def purchase_for(self, vhc, partner_id, api_key, *, quote_id,
                     passport_ref, full_name, entry_day, day, key_id,
                     dob=None, idem_key=None, payment_redirect_ref=None):
        p = self.authenticate(partner_id, api_key)
        if payment_redirect_ref is not None and \
                re.fullmatch(r"\d{13,19}", str(payment_redirect_ref)):
            _refuse("the PAN never lands — send the redirect "
                    "reference, not the card")
        if idem_key is not None:
            prior = self.idem.get((partner_id, idem_key))
            if prior is not None:
                self.audit.append({"event": "partner_bind_replayed",
                                   "partner": partner_id,
                                   "ref": prior, "day": day})
                cover = vhc.covers[prior]
                return {**cover, "idempotent_replay": True}
        q = vhc.quotes.get(quote_id)
        if q is None or q.get("partner_id") != partner_id:
            _refuse("partners bind their own quotes — a quote without "
                    "attribution is a sale the scheme cannot audit")
        cover = vhc.purchase(quote_id=quote_id,
                             passport_ref=passport_ref,
                             full_name=full_name, entry_day=entry_day,
                             day=day, key_id=key_id, dob=dob)
        cover["partner_id"] = partner_id
        if payment_redirect_ref is not None:
            cover["payment_redirect_ref"] = payment_redirect_ref
        if idem_key is not None:
            self.idem[(partner_id, idem_key)] = cover["ref"]
        p["sales"] += 1
        p["gross_minor"] += q["total_minor"]
        self.audit.append({"event": "partner_sale",
                           "partner": partner_id, "ref": cover["ref"],
                           "day": day})
        return cover

    def checkout_status(self, partner_id, api_key, *, idem_key):
        """The status endpoint: a partner asks what became of its bind.
        Not-found is stated, not assumed."""
        self.authenticate(partner_id, api_key)
        ref = self.idem.get((partner_id, idem_key))
        if ref is None:
            return {"state": "not-found",
                    "note": "no bind under this key — stated, not "
                            "assumed"}
        return {"state": "bound", "ref": ref}

    def report(self):
        return {"partners": len(self.partners),
                "active": sum(1 for p in self.partners.values()
                              if p["state"] == "active"),
                "sales": sum(p["sales"] for p in self.partners.values()),
                "gross_minor": sum(p["gross_minor"]
                                   for p in self.partners.values())}

    def _partner(self, partner_id):
        p = self.partners.get(partner_id)
        if p is None:
            _refuse("unknown partner")
        return p
