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
                 day, key_id):
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
            "full_name": full_name, "window": window,
            "extensions": [], "profile_version": self.profile_version,
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
        return {
            "iss": self.issuer, "v": cover["profile_version"],
            "ref": cover["ref"], "name": cover["full_name"],
            "pp": cover["passport_sha256"],
            "win": cover["window"], "ext": list(cover["extensions"]),
            "scope": list(self.profile["scope"]),
            "vu": cover["purchased_day"] + self.profile["qr_offline_days"],
        }

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

    # --- verification ------------------------------------------------
    def revoke(self, ref, *, reason, day):
        self._cover(ref)
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

    def verify(self, token, *, day, cache=None):
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
            win = claims.get("win", {})
            to_day = win.get("to", -1)
            if day > to_day + self.profile["grace_days"]:
                reasons.append("lapsed")
            elif day > to_day:
                reasons.append("grace window — extend at the desk")
            flag = next((f for f in self.flags if f["ref"] == claims["ref"]
                         and day - f["day"] <=
                         self.profile["humanitarian_days"]), None)
            if flag is not None:
                reasons.append("humanitarian regularisation pending")
            if (not reasons and
                    to_day - day <= self.profile["expiring_soon_days"]):
                reasons.append("cover expires within 72h")
        if cache is not None and cache_age > cache["max_age_days"]:
            reasons.append("verification data stale — escalate")
        if not ok or any(r.startswith(("unverifiable", "revoked",
                                       "lapsed", "profile mismatch",
                                       "qr beyond", "verification data"))
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
                "humanitarian_flags": len(self.flags),
                "collected_minor": sum(p["minor"] for p in self.payments),
                "currency": self.profile["currency"]}
