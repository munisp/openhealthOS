"""vhcover.py — country-agnostic visitor health cover: signed offline QR,
green/amber/red border verdicts, and configurable pricing — the same engine
serves Cabo Verde's €25/30-day block scheme and Nigeria's flat+per-day
scheme because every rule lives in a versioned country profile, never in
an `if country ==` branch.

Modules this implements from a VHC-class spec (e.g. CV-VHC-PLAT-SPEC):
  - FR-SAL-01/06: config-driven quote (block or flat+per-day models, age
    bands, extensions that keep cover contiguous, 24h quote validity).
  - FR-VER-01/05: HSM-style signed QR (compact JWS, HS256, key rotation),
    offline verification from a cached key ring + revocation snapshot,
    green/amber/red verdict matrix, humanitarian regularisation flags.

Doctrines:
- The QR is a signed CLAIM, not a pointer: an officer verifies it with a
  cached key ring and a revocation snapshot, no network required.
- Silence is a finding: an unknown key id, a stale cache, or a payload
  past its offline validity is a RED verdict with a named reason.
- A verdict names its reasons and its evidence. GREEN without evidence
  is a bug.
- Money is minor units of the profile's currency; an alt currency is a
  display courtesy at a stated rate, never a second ledger.
- Deploying to a new country is adding a profile entry, not a fork.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

VHCOVER_VERSION = "vhcover@1.0.0"

# Versioned country profiles — a rule that cannot be traced to a table
# is a bribe with a receipt. Model "block": ceil(days/block_days) blocks
# at block_price_minor; extension_price_minor per block. Model
# "flat_per_day": flat_fee_minor + per_day_minor * days.
VHC_PROFILES = {
    "cv-vhc@1.0.0": {
        "country": "cv",
        "currency": "EUR",
        "model": "block",
        "block_days": 30,
        "block_price_minor": 2500,           # €25.00 per 30 days
        "extension_price_minor": 1000,       # +€10.00 per 30-day block
        "age_bands": ({"max_age": 2, "factor": 0.0},
                      {"max_age": 12, "factor": 0.5},
                      {"max_age": None, "factor": 1.0}),
        "alt_currency": ("CVE", 110.265),    # escudo, BCV peg
        "max_covered_days": None,
        "quote_valid_days": 1,
        "grace_days": 1,                     # BR-06 grace window
        "expiring_soon_days": 3,             # amber at ≤72h to expiry
        "humanitarian_days": 3,              # 72h regularisation flag
        "qr_offline_days": 90,               # offline-verifiable validity
        "cache_max_age_days": 3,             # ≥72h offline cache budget
        "scope": ("emergency_medical", "medevac",
                  "repatriation_of_remains"),
        # §5.2 exemption decision table — versioned like everything else
        "exemption_categories": ("resident", "transit", "crew",
                                 "diplomatic", "foreign_policy"),
        "transit_max_days": 2,               # airside transit window
        "foreign_policy_floor_minor": 5_000_000,  # ≥ €50,000 own cover
    },
    "ng-vhc@1.0.0": {
        "country": "ng",
        "currency": "NGN",
        "model": "flat_per_day",
        "flat_fee_minor": 500_000,           # ₦5,000 entry fee
        "per_day_minor": 50_000,             # ₦500 per covered day
        "extension_price_minor": None,       # extensions priced per-day
        "age_bands": ({"max_age": None, "factor": 1.0},),
        "alt_currency": None,
        "max_covered_days": 90,              # beyond is residence
        "quote_valid_days": 1,
        "grace_days": 1,
        "expiring_soon_days": 3,
        "humanitarian_days": 3,
        "qr_offline_days": 90,
        "cache_max_age_days": 3,
        "scope": ("emergency_medical",),
        "exemption_categories": ("resident", "transit", "crew",
                                 "diplomatic", "foreign_policy"),
        "transit_max_days": 2,
        "foreign_policy_floor_minor": 10_000_000,  # ≥ ₦100,000 own cover
    },
}

VERDICTS = ("green", "amber", "red")
OFFICER_ACTION = {"green": "admit", "amber": "desk", "red": "supervisor"}


class VHCError(Exception):
    pass


def _refuse(msg):
    raise VHCError(msg)


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(text):
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except Exception:
        _refuse("malformed base64url segment")


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _age_factor(profile, age_years):
    for band in profile["age_bands"]:
        if band["max_age"] is None or age_years <= band["max_age"]:
            return band["factor"]
    return 1.0


def price_cover(profile, *, days, age_years, extension=False):
    """The premium from the profile. Returns (total_minor, units) where
    units is blocks or covered days, stated so the receipt can show its
    arithmetic."""
    if not isinstance(days, int) or days < 1:
        _refuse("cover window must be at least one day")
    if age_years is None or age_years < 0:
        _refuse("traveller age is required — absence is not a discount")
    cap = profile.get("max_covered_days")
    if cap is not None and days > cap:
        _refuse("window beyond the tourism cap is residence, not cover")
    if profile["model"] == "block":
        blocks = -(-days // profile["block_days"])
        unit = (profile["extension_price_minor"] if extension
                else profile["block_price_minor"])
        if unit is None:
            _refuse("profile does not price extensions")
        total = blocks * unit
        units = blocks
    else:
        total = (0 if extension else profile["flat_fee_minor"])
        total += profile["per_day_minor"] * days
        units = days
    factor = _age_factor(profile, age_years)
    return int(round(total * factor)), units


def display_amount(profile, total_minor):
    """Human-readable amount; alt currency (e.g. CVE at the stated peg)
    is a courtesy display, never a second ledger."""
    cur = profile["currency"]
    out = {"currency": cur, "minor": total_minor,
           "major": round(total_minor / 100, 2)}
    alt = profile.get("alt_currency")
    if alt:
        out["alt"] = {"currency": alt[0],
                      "major": round(total_minor * alt[1] / 100, 2),
                      "rate": alt[1]}
    return out


class KeyRing:
    """Versioned signing keys with rotation windows. In production the
    private half lives in an HSM; here the ring is the trust boundary,
    and rotation is a first-class act, not a redeploy."""

    def __init__(self):
        self.keys = {}
        self.audit = []

    def register(self, key_id, secret, *, active_from, active_to=None):
        if not key_id or not secret:
            _refuse("a key needs an id and a secret")
        self.keys[key_id] = {"secret": secret, "active_from": active_from,
                             "active_to": active_to}
        self.audit.append({"event": "key_registered", "key_id": key_id,
                           "active_from": active_from,
                           "active_to": active_to})

    def get(self, key_id):
        return self.keys.get(key_id)

    def active(self, key_id, day):
        key = self.keys.get(key_id)
        if key is None:
            return None
        if day < key["active_from"]:
            return None
        if key["active_to"] is not None and day > key["active_to"]:
            return None
        return key

    def snapshot(self):
        return {kid: dict(k) for kid, k in self.keys.items()}

    @classmethod
    def from_snapshot(cls, snap):
        ring = cls()
        ring.keys = {kid: dict(k) for kid, k in snap.items()}
        return ring


def sign_claims(claims, *, key_ring, key_id, day):
    """Compact JWS (HS256). The signature is over the canonical payload —
    the checksum is the contract."""
    if key_ring.active(key_id, day) is None:
        _refuse("signing key is not active — silence is a finding")
    secret = key_ring.keys[key_id]["secret"].encode()
    header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
    signing_input = _b64u(_canon(header)) + "." + _b64u(_canon(claims))
    sig = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64u(sig)


def read_qr(token, *, key_ring, day):
    """Parse and verify a QR token. Returns (claims, ok, reasons). Never
    raises for bad input — a bad QR is a RED verdict, not a crash."""
    parts = token.split(".")
    if len(parts) != 3:
        return None, False, ["unverifiable: malformed"]
    try:
        header = json.loads(_b64u_dec(parts[0]))
    except Exception:
        return None, False, ["unverifiable: malformed header"]
    if header.get("alg") != "HS256" or "kid" not in header:
        return None, False, ["unverifiable: unsupported header"]
    key = key_ring.get(header["kid"])
    if key is None:
        return None, False, ["unverifiable: unknown key"]
    if day < key["active_from"] or (key["active_to"] is not None
                                    and day > key["active_to"]):
        return None, False, ["unverifiable: key not active"]
    expected = hmac.new(key["secret"].encode(),
                        (parts[0] + "." + parts[1]).encode(),
                        hashlib.sha256).digest()
    try:
        actual = _b64u_dec(parts[2])
    except VHCError:
        return None, False, ["unverifiable: malformed signature"]
    if not hmac.compare_digest(expected, actual):
        return None, False, ["unverifiable: bad signature"]
    try:
        claims = json.loads(_b64u_dec(parts[1]))
    except Exception:
        return None, False, ["unverifiable: malformed payload"]
    return claims, True, []


class VHCCoverRegistry:
    """Quote → purchase → signed QR → verify/extend/revoke, all under one
    country profile. Ledgers are append-only; verdicts name reasons."""

    def __init__(self, *, profile_version, key_ring, issuer):
        if profile_version not in VHC_PROFILES:
            _refuse("unknown country profile: %s" % profile_version)
        self.profile_version = profile_version
        self.profile = dict(VHC_PROFILES[profile_version])
        self.key_ring = key_ring
        self.issuer = issuer
        self.quotes = {}
        self.covers = {}
        self.exemptions = {}        # ref -> exemption record
        self.revocations = []       # append-only: {"ref", "reason", "day"}
        self.flags = []             # append-only humanitarian flags
        self.payments = []          # append-only minor-unit ledger
        self.audit = []

    # --- sales -------------------------------------------------------
    def quote(self, *, days, age_years, day):
        total, units = price_cover(self.profile, days=days,
                                   age_years=age_years)
        quote_id = "Q-" + secrets.token_hex(4)
        self.quotes[quote_id] = {
            "quote_id": quote_id, "days": days, "age_years": age_years,
            "total_minor": total, "units": units,
            "amount": display_amount(self.profile, total),
            "issued_day": day,
            "expires_day": day + self.profile["quote_valid_days"],
            "profile_version": self.profile_version,
        }
        return self.quotes[quote_id]

    def purchase(self, *, quote_id, passport_ref, full_name, entry_day,
                 day, key_id, dob=None, photo_sha256=None):
        q = self.quotes.get(quote_id)
        if q is None:
            _refuse("unknown quote — a price without a table is a bribe")
        if day > q["expires_day"]:
            _refuse("quote expired — re-quote at the counter")
        if not passport_ref or not full_name:
            _refuse("cover binds to a passport and a name")
        ref = "VHC-" + secrets.token_hex(4).upper()
        window = {"from": entry_day, "to": entry_day + q["days"] - 1}
        cover = {
            "ref": ref, "passport_sha256": hashlib.sha256(
                passport_ref.encode()).hexdigest(),
            "dob_sha256": (hashlib.sha256(dob.encode()).hexdigest()
                           if dob else None),
            "photo_sha256": photo_sha256,
            "full_name": full_name, "window": window,
            "extensions": [], "corrections": [], "refunds": [],
            "profile_version": self.profile_version,
            "quote": q, "purchased_day": day,
        }
        self.covers[ref] = cover
        if q["total_minor"] > 0:
            self.payments.append({"ref": ref, "kind": "premium",
                                  "minor": q["total_minor"], "day": day})
        else:
            self.payments.append({"ref": ref, "kind": "exempt-issue",
                                  "minor": 0, "day": day})
        claims = self._claims(cover)
        cover["qr"] = sign_claims(claims, key_ring=self.key_ring,
                                  key_id=key_id, day=day)
        self.audit.append({"event": "issued", "ref": ref, "day": day})
        return cover

    def _claims(self, cover):
        claims = {
            "iss": self.issuer, "v": cover["profile_version"],
            "kind": "cover",
            "ref": cover["ref"], "name": cover["full_name"],
            "pp": cover["passport_sha256"],
            "win": cover["window"], "ext": list(cover["extensions"]),
            "scope": list(self.profile["scope"]),
            "vu": cover["purchased_day"] + self.profile["qr_offline_days"],
        }
        if cover.get("dob_sha256"):
            claims["dob"] = cover["dob_sha256"]
        if cover.get("photo_sha256"):
            claims["ph"] = cover["photo_sha256"]
        return claims

    def extend(self, ref, *, extra_days, day, key_id):
        """Extensions must keep cover contiguous: the new window starts
        the day after the old one ends. A cover lapsed beyond the grace
        window is not extended — it is re-purchased."""
        cover = self._cover(ref)
        if day > cover["window"]["to"] + self.profile["grace_days"]:
            _refuse("cover lapsed beyond grace — re-purchase, "
                    "do not backdate")
        total, units = price_cover(self.profile, days=extra_days,
                                   age_years=99, extension=True)
        block_days = (self.profile["block_days"]
                      if self.profile["model"] == "block" else 1)
        cover["window"]["to"] += units * block_days
        cover["extensions"].append({"days": extra_days, "units": units,
                                    "minor": total, "day": day})
        self.payments.append({"ref": ref, "kind": "extension",
                              "minor": total, "day": day})
        cover["qr"] = sign_claims(self._claims(cover),
                                  key_ring=self.key_ring,
                                  key_id=key_id, day=day)
        self.audit.append({"event": "extended", "ref": ref, "day": day})
        return cover

    # --- exemptions (spec §5.2 decision table, as config) -------------
    def register_exemption(self, *, passport_ref, full_name, category,
                           evidence_ref, day, expires_day, key_id,
                           dob=None, foreign_cover_minor=None):
        """An exemption is a signed, evidence-backed record — never a
        verbal wave-through. Every category needs documentary evidence;
        foreign-policy exemptions must prove their own cover meets the
        profile floor; transit stays inside the transit window."""
        if category not in self.profile["exemption_categories"]:
            _refuse("unknown exemption category — the table is the law")
        if not evidence_ref:
            _refuse("an exemption without evidence is a favour")
        if not passport_ref or not full_name:
            _refuse("an exemption binds to a passport and a name")
        if expires_day < day:
            _refuse("an exemption born expired is a clerical error")
        if category == "transit" and \
                expires_day - day > self.profile["transit_max_days"]:
            _refuse("transit exemptions live inside the transit window")
        if category == "foreign_policy":
            floor = self.profile["foreign_policy_floor_minor"]
            if foreign_cover_minor is None or foreign_cover_minor < floor:
                _refuse("foreign-policy cover below the floor is not "
                        "cover — top up or buy the scheme")
        ref = "EXM-" + secrets.token_hex(4).upper()
        ex = {
            "ref": ref, "kind": "exemption",
            "passport_sha256": hashlib.sha256(
                passport_ref.encode()).hexdigest(),
            "dob_sha256": (hashlib.sha256(dob.encode()).hexdigest()
                           if dob else None),
            "full_name": full_name, "category": category,
            "evidence_ref": evidence_ref,
            "foreign_cover_minor": foreign_cover_minor,
            "issued_day": day, "expires_day": expires_day,
            "profile_version": self.profile_version,
        }
        self.exemptions[ref] = ex
        claims = {
            "iss": self.issuer, "v": self.profile_version,
            "kind": "exemption", "ref": ref, "name": full_name,
            "pp": ex["passport_sha256"], "cat": category,
            "exp": expires_day,
            "vu": day + self.profile["qr_offline_days"],
        }
        if ex["dob_sha256"]:
            claims["dob"] = ex["dob_sha256"]
        ex["qr"] = sign_claims(claims, key_ring=self.key_ring,
                               key_id=key_id, day=day)
        self.audit.append({"event": "exemption_issued", "ref": ref,
                           "category": category, "day": day})
        return ex

    # --- desk service (S-024): lookup by passport, reissue ------------
    def desk_lookup(self, *, passport_ref, dob=None):
        """The desk finds a traveller by passport (+dob when recorded).
        Zero matches is a finding, not an error: no record is not no
        history."""
        if not passport_ref:
            _refuse("desk lookup needs a passport reference")
        pp = hashlib.sha256(passport_ref.encode()).hexdigest()
        dob_h = hashlib.sha256(dob.encode()).hexdigest() if dob else None
        matches = []
        for c in self.covers.values():
            if c["passport_sha256"] != pp:
                continue
            if dob_h and c.get("dob_sha256") and c["dob_sha256"] != dob_h:
                continue
            matches.append({"kind": "cover", "ref": c["ref"],
                            "name": c["full_name"],
                            "window": dict(c["window"])})
        for e in self.exemptions.values():
            if e["passport_sha256"] != pp:
                continue
            if dob_h and e.get("dob_sha256") and e["dob_sha256"] != dob_h:
                continue
            matches.append({"kind": "exemption", "ref": e["ref"],
                            "name": e["full_name"],
                            "category": e["category"],
                            "expires_day": e["expires_day"]})
        return {"matches": matches, "count": len(matches),
                "note": ("no record on file — a traveller the scheme has "
                         "not met" if not matches else None)}

    def reissue(self, ref, *, day, key_id):
        """Reissue the QR from the desk. The record is unchanged; only a
        fresh signature on the current claims leaves the building, and
        the old QR keeps verifying until its key retires — revocation is
        a separate, reasoned act."""
        if ref in self.covers:
            cover = self.covers[ref]
            cover["qr"] = sign_claims(self._claims(cover),
                                      key_ring=self.key_ring,
                                      key_id=key_id, day=day)
            self.audit.append({"event": "reissued", "ref": ref,
                               "day": day})
            return cover["qr"]
        if ref in self.exemptions:
            ex = self.exemptions[ref]
            claims = {
                "iss": self.issuer, "v": self.profile_version,
                "kind": "exemption", "ref": ref, "name": ex["full_name"],
                "pp": ex["passport_sha256"], "cat": ex["category"],
                "exp": ex["expires_day"],
                "vu": day + self.profile["qr_offline_days"],
            }
            if ex["dob_sha256"]:
                claims["dob"] = ex["dob_sha256"]
            ex["qr"] = sign_claims(claims, key_ring=self.key_ring,
                                   key_id=key_id, day=day)
            self.audit.append({"event": "reissued", "ref": ref,
                               "day": day})
            return ex["qr"]
        _refuse("unknown ref — reissue serves records, not requests")

    # --- corrections & refunds (S-009, S-020) -------------------------
    def correct_cover(self, ref, *, new_name=None, by, day, key_id,
                      supervisor=None):
        """S-009: one free name correction before travel; a second
        correction needs a supervisor. Passports and windows never
        correct — a new traveller is a new cover, not an edit."""
        cover = self._cover(ref)
        if not new_name:
            _refuse("the only correctable field is the name spelling")
        if not by:
            _refuse("a correction names its agent")
        if cover["corrections"] and supervisor is None:
            _refuse("a second correction needs a supervisor — one free "
                    "fix, then four-eyes")
        cover["full_name"] = new_name
        cover["corrections"].append({"by": by, "day": day,
                                     "supervisor": supervisor})
        cover["qr"] = sign_claims(self._claims(cover),
                                  key_ring=self.key_ring,
                                  key_id=key_id, day=day)
        self.audit.append({"event": "corrected", "ref": ref, "by": by,
                           "day": day})
        return cover

    def refund_cover(self, ref, *, reason, day, evidence_ref=None):
        """S-020: full refund before the window opens; pro-rata over
        unused full blocks after; visa-refusal refunds carry their
        evidence. The refund states its arithmetic and its SLA clock —
        the instrument is always the one that paid."""
        cover = self._cover(ref)
        if cover["refunds"]:
            _refuse("already refunded — a second refund is the scheme "
                    "paying twice")
        if reason not in ("pre-travel", "early-exit", "visa-refusal"):
            _refuse("unknown refund reason — the table is the law")
        if reason == "visa-refusal" and not evidence_ref:
            _refuse("a visa-refusal refund without the refusal letter "
                    "is a gift")
        q = cover["quote"]
        paid = q["total_minor"]
        if day < cover["window"]["from"]:
            refund_minor = paid
            basis = "full — the window never opened"
        else:
            # pro-rata over the ORIGINAL purchase; extensions are
            # separate purchases with their own refunds
            total_days = q["days"]
            used_days = max(0, day - cover["window"]["from"])
            if self.profile["model"] == "block":
                block = self.profile["block_days"]
                blocks = max(1, -(-total_days // block))
                unused_full = max(0, (total_days - used_days) // block)
                unit = paid / blocks
                refund_minor = int(round(unused_full * unit))
                basis = ("pro-rata over %d unused full %d-day blocks "
                         "of the original purchase"
                         % (unused_full, block))
            else:
                unused = max(0, total_days - used_days)
                refund_minor = self.profile["per_day_minor"] * unused
                basis = "pro-rata over %d unused days" % unused
        self.payments.append({"ref": ref, "kind": "refund",
                              "minor": -refund_minor, "day": day})
        cover["refunds"].append({
            "reason": reason, "refund_minor": refund_minor,
            "basis": basis, "day": day, "evidence_ref": evidence_ref,
            "instrument": "original — refunds return the way the money "
                          "came",
            "sla_business_days": 10, "sla_day": day + 14})
        self.audit.append({"event": "refunded", "ref": ref,
                           "refund_minor": refund_minor, "day": day,
                           "reason": reason})
        return cover["refunds"][-1]

    # --- verification ------------------------------------------------
    def revoke(self, ref, *, reason, day):
        if ref not in self.covers and ref not in self.exemptions:
            _refuse("unknown ref — revocation serves records, not "
                    "requests")
        if not reason:
            _refuse("a revocation without a reason is a disappearance")
        self.revocations.append({"ref": ref, "reason": reason, "day": day})
        self.audit.append({"event": "revoked", "ref": ref, "day": day})

    def humanitarian_flag(self, ref, *, day, note):
        self._cover(ref)
        if not note:
            _refuse("a humanitarian flag without a note is a whim")
        self.flags.append({"ref": ref, "day": day, "note": note})

    def offline_cache(self, *, as_of):
        """Everything an officer needs for ≥72h offline: the key ring and
        the revocation list, stamped. Past max age the cache is evidence
        of staleness, not evidence of cover."""
        return {"keys": self.key_ring.snapshot(),
                "revocations": [r["ref"] for r in self.revocations],
                "stamped": as_of, "profile_version": self.profile_version,
                "max_age_days": self.profile["cache_max_age_days"]}

    def verify(self, token, *, day, cache=None,
               presented_photo_sha256=None):
        """The verdict matrix. Online (cache=None) verifies against the
        live ring; offline verifies against a cache whose age is stated
        in the evidence. Every verdict carries reasons, an officer
        action, and a measured latency against the 2s budget."""
        start = time.perf_counter()
        reasons, mode = [], ("offline" if cache else "online")
        if cache is not None:
            cache_age = day - cache["stamped"]
            ring = KeyRing.from_snapshot(cache["keys"])
            revoked = set(cache["revocations"])
        else:
            cache_age = 0
            ring, revoked = self.key_ring, {r["ref"]
                                            for r in self.revocations}
        claims, ok, sig_reasons = read_qr(token, key_ring=ring, day=day)
        reasons.extend(sig_reasons)
        if ok:
            if claims.get("v") != self.profile_version:
                reasons.append("profile mismatch")
            if claims["ref"] in revoked:
                reasons.append("revoked")
            if day > claims.get("vu", -1):
                reasons.append("qr beyond offline validity — "
                               "re-verify online")
            if claims.get("kind") == "exemption":
                if not reasons and day > claims.get("exp", -1):
                    reasons.append("exemption expired")
            else:
                win = claims.get("win", {})
                to_day = win.get("to", -1)
                if day > to_day + self.profile["grace_days"]:
                    reasons.append("lapsed")
                elif day > to_day:
                    reasons.append("grace window — extend at the desk")
                flag = next((f for f in self.flags
                             if f["ref"] == claims["ref"] and
                             day - f["day"] <=
                             self.profile["humanitarian_days"]), None)
                if flag is not None:
                    reasons.append("humanitarian regularisation pending")
                if (not reasons and
                        to_day - day <= self.profile["expiring_soon_days"]):
                    reasons.append("cover expires within 72h")
            if (presented_photo_sha256 is not None and
                    claims.get("ph") is not None and
                    presented_photo_sha256 != claims["ph"]):
                reasons.append("photo mismatch — to the desk")
        if cache is not None and cache_age > cache["max_age_days"]:
            reasons.append("verification data stale — escalate")
        if not ok or any(r.startswith(("unverifiable", "revoked",
                                       "lapsed", "profile mismatch",
                                       "qr beyond", "verification data",
                                       "exemption expired"))
                         for r in reasons):
            verdict = "red"
        elif reasons:
            verdict = "amber"
        else:
            verdict = "green"
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "verdict": verdict, "reasons": reasons,
            "officer_action": OFFICER_ACTION[verdict],
            "ref": claims.get("ref") if claims else None,
            "evidence": {"mode": mode, "cache_age_days": cache_age,
                         "latency_ms": elapsed_ms,
                         "latency_budget_ms": 2000,
                         "within_budget": elapsed_ms <= 2000},
            "claims": claims if ok else None,
        }

    def _cover(self, ref):
        cover = self.covers.get(ref)
        if cover is None:
            _refuse("unknown cover ref")
        return cover

    def report(self):
        """Denominators always visible."""
        return {"profile": self.profile_version,
                "quotes": len(self.quotes), "covers": len(self.covers),
                "exempt_covers": sum(1 for p in self.payments
                                     if p["kind"] == "exempt-issue"),
                "extensions": sum(len(c["extensions"])
                                  for c in self.covers.values()),
                "revocations": len(self.revocations),
                "exemptions": len(self.exemptions),
                "humanitarian_flags": len(self.flags),
                "collected_minor": sum(p["minor"] for p in self.payments),
                "currency": self.profile["currency"]}
