"""telesupervision.py — asynchronous doctor supervision over the mesh.

One doctor, twenty clinics. The CHW's protocol session (or any structured
encounter) becomes a signed case bundle; the doctor reviews the morning
queue wherever connectivity exists; the signed review travels back the
same way. Store-and-forward is not a degraded telemedicine — it is the
only telemedicine that survives the infrastructure.

Doctrine:
- Both directions are HMAC-signed: the CHW cannot silently edit after
  submission, the doctor's review is attributable advice.
- Reviews are ADVISORY-STRUCTURED: approve / modify / take-over. A
  take-over converts to an in-person or live referral — the bundle
  records which.
- Queue order is triage-driven (escalations first), not arrival order.
"""
import hashlib
import hmac
import json


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _sign(body, key):
    return hmac.new(key, _canon(body), hashlib.sha256).hexdigest()


def _verify(body, sig, key):
    return hmac.compare_digest(_sign(body, key), sig)


class CaseBundle:
    @staticmethod
    def create(author, patient_ref, content, key, now=None):
        """content: {triage_path, vitals, protocol_outcome, note_refs}.
        patient_ref is PSEUDONYMOUS by the lakefeed contract."""
        body = {"type": "oh.case-bundle", "author": author,
                "patient_ref": patient_ref, "content": content, "at": now}
        return {**body, "sig": _sign(body, key)}

    @staticmethod
    def verify(bundle, key):
        sig = bundle.pop("sig", None)
        ok = sig and _verify(bundle, sig, key)
        bundle["sig"] = sig
        return bool(ok)


class ReviewBoard:
    """The doctor's morning queue. Escalations float; tampered bundles
    surface as quarantined, never silently dropped."""

    def __init__(self, key):
        self.key = key
        self.bundles = []

    def submit(self, bundle):
        ok = CaseBundle.verify(dict(bundle), self.key)
        self.bundles.append({**bundle, "verified": ok,
                             "reviewed": False})
        return ok

    def queue(self):
        open_b = [b for b in self.bundles if not b["reviewed"]]
        esc = [b for b in open_b if b["content"].get("protocol_outcome", {}).get("escalate")
               or b["content"].get("escalate")]
        rest = [b for b in open_b if b not in esc]
        quarantined = [b for b in open_b if not b["verified"]]
        return {"escalations": esc, "routine": rest,
                "quarantined": quarantined}

    def review(self, bundle_sig, doctor, decision, note, key, now=None):
        if decision not in ("approve", "modify", "take-over"):
            raise ValueError(f"unknown decision: {decision}")
        for b in self.bundles:
            if b["sig"] == bundle_sig and not b["reviewed"]:
                if not b["verified"]:
                    return {"ok": False, "reason": "quarantined-cannot-review"}
                body = {"type": "oh.case-review", "bundle_sig": bundle_sig,
                        "doctor": doctor, "decision": decision,
                        "note": note, "at": now}
                b["reviewed"] = True
                b["review"] = {**body, "sig": _sign(body, key)}
                return {"ok": True, "review": b["review"]}
        return {"ok": False, "reason": "not-found-or-reviewed"}
