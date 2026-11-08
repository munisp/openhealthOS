"""healthcred.py — verifiable health credentials (the NHA CARD pattern).

Cabo Verde's NHA CARD proved the shape: a signed digital certificate a
holder presents and a verifier checks offline. This package generalises
it — vaccination, test-result, and medical-certificate credentials —
on top of the qrtrust keyring (HMAC tag + revocation list + offline
verification budget).

Doctrine:
- THE CREDENTIAL IS A SIGNED TAG: payload fields plus an HMAC signature
  under a named key. Verification needs only the keyring — no network.
- REVOCATION IS A LIST, NOT A HOPE: the verifier carries a revocation
  set; a revoked credential refuses as REVOKED, naming the state.
- EXPIRY IS IN THE PAYLOAD: a credential that never expires is a
  forgery waiting; every credential states its valid-until day.
- THE HOLDER'S IDENTITY IS HASHED: the tag carries a salted subject
  hash, never a bare identifier — presentation proves possession,
  not identity disclosure.
- VERIFICATION IS AUDITED: every verification attempt appends to an
  append-only log; offline verification is still accountable.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "qrtrust"))
from keyring import (RevokedError, TrustError, sign,  # noqa: E402
                     verify_with_ring)

VERSION = "healthcred@1.0.0"

CRED_TYPES = ("vaccination", "test-result", "medical-certificate")

# qrtrust v2 tag: <prefix>.<kid>.<f1>...<fN>.<sig>
PREFIX = "OHC"
FIELDS = 5  # subject_hash.cred_type.statement_hash.valid_until.serial


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def subject_hash(subject_ref, salt):
    """Salted subject hash — the tag never carries a bare identifier."""
    if not salt:
        raise Error("a subject hash requires a salt")
    return hashlib.sha256(f"{salt}:{subject_ref}".encode()).hexdigest()[:12]


class CredentialWallet:
    """Issuance side: mint signed credentials against a keyring."""

    def __init__(self, ring, active_kid, *, issuer, salt):
        if active_kid not in ring:
            raise Error(f"active key {active_kid!r} not in ring")
        if not issuer:
            raise Error("a wallet names its issuer")
        self.ring = ring
        self.active_kid = active_kid
        self.issuer = issuer
        self.salt = salt
        self.issued = {}

    def mint(self, serial, subject_ref, cred_type, statement, *,
             valid_until, issued_day, by):
        if cred_type not in CRED_TYPES:
            raise Error(f"unknown credential type {cred_type!r}; types "
                        f"are {CRED_TYPES}")
        if serial in self.issued:
            raise Error(f"serial {serial} already minted")
        if not statement:
            raise Error("a credential carries a statement")
        if valid_until <= issued_day:
            raise Error("valid-until must follow the issue day — a "
                        "credential that never expires is a forgery "
                        "waiting")
        if by != self.issuer:
            raise Error("only the wallet's issuer mints from it")
        fields = [subject_hash(subject_ref, self.salt), cred_type,
                  _sha({"statement": statement, "serial": serial})[:16],
                  str(valid_until), serial]
        body = ".".join([PREFIX, self.active_kid, *fields])
        tag = f"{body}.{sign(self.ring[self.active_kid], body)}"
        self.issued[serial] = {
            "serial": serial, "subject_hash": fields[0],
            "cred_type": cred_type, "statement": statement,
            "valid_until": valid_until, "issued_day": issued_day,
            "tag": tag, "issuer": self.issuer, "version": VERSION,
        }
        return self.issued[serial]


class Verifier:
    """Presentation side: offline verification with revocation and
    expiry, every attempt audited."""

    def __init__(self, ring, *, revoked=None, salt=""):
        self.ring = ring
        self.revoked = set(revoked or ())
        self.salt = salt
        self.audit_log = []

    def verify(self, tag, *, verifier, day, subject_ref=None):
        """Verify a presented tag. Returns the decoded credential view
        or raises TrustError/RevokedError/Error — every path audited."""
        entry = {"event": "verify", "day": day, "verifier": verifier}
        if not verifier:
            raise Error("every verification names its verifier")
        try:
            fields = verify_with_ring(tag, self.ring, PREFIX, FIELDS,
                                      revoked=self.revoked)
        except (TrustError, RevokedError) as exc:
            entry["result"] = "refused"
            entry["reason"] = str(exc)
            self.audit_log.append(entry)
            raise
        subj, cred_type, stmt_hash, valid_until, serial = fields
        body = ".".join([PREFIX, *tag.strip().split(".")[1:-1]])
        if int(valid_until) < day:
            entry["result"] = "refused"
            entry["reason"] = f"expired day {valid_until}"
            self.audit_log.append(entry)
            raise Error(f"credential expired on day {valid_until}")
        subject_checked = None
        if subject_ref is not None:
            if not self.salt:
                raise Error("verifier has no salt; subject check "
                            "impossible — possession-only verification "
                            "must be chosen, not defaulted")
            subject_checked = (subject_hash(subject_ref, self.salt)
                               == subj)
            if not subject_checked:
                entry["result"] = "refused"
                entry["reason"] = "subject mismatch"
                self.audit_log.append(entry)
                raise Error("credential does not belong to the "
                            "presenter")
        entry["result"] = "valid"
        entry["serial"] = serial
        self.audit_log.append(entry)
        return {"serial": serial, "cred_type": cred_type,
                "statement_hash": stmt_hash,
                "valid_until": int(valid_until),
                "subject_checked": subject_checked,
                "body": body, "version": VERSION}

    def audit(self):
        return {"verifications": len(self.audit_log),
                "refused": sum(1 for e in self.audit_log
                               if e["result"] == "refused"),
                "sha256": _sha(self.audit_log), "version": VERSION}
