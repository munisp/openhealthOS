"""NIN verification adapter (ADR-0057). Bridges to NIMC verification; the
network call is INJECTED (verifier callable) so production wires the real
NIMC endpoint while tests and offline edges use cached attestations.
Doctrine: fail-soft — unverified is a state, not a block; attestations are
cached with TTL so wards keep working when NIMC is unreachable.
"""
from __future__ import annotations
import hashlib, json, sys, time

TTL_SECONDS = 30 * 86400  # cached attestation valid 30 days

class NinError(Exception):
    pass

def _norm(nin: str) -> str:
    n = "".join(ch for ch in nin if ch.isdigit())
    if len(n) != 11:
        raise NinError(f"NIN must be 11 digits, got {len(n)}")
    return n

def _fingerprint(nin: str) -> str:
    """We never store the raw NIN — NDPA data-minimization."""
    return hashlib.sha256(("oh-nin:" + nin).encode()).hexdigest()[:24]

class NinAdapter:
    def __init__(self, verifier=None, now=None, ttl=TTL_SECONDS):
        """verifier(nin) -> {"verified": bool, "name_match": bool|None}."""
        self.verifier = verifier
        self.now = now or (lambda: int(time.time()))
        self.ttl = ttl
        self.cache = {}  # fingerprint -> {"verified":bool,"at":int}

    def verify(self, nin: str, allow_cached=True) -> dict:
        n = _norm(nin)
        fp = _fingerprint(n)
        if allow_cached and fp in self.cache:
            c = self.cache[fp]
            if self.now() - c["at"] <= self.ttl:
                return {"state": "verified-cached" if c["verified"] else "rejected-cached",
                        "verified": c["verified"], "fingerprint": fp, "fresh": False}
        if self.verifier is None:
            return {"state": "unverified", "verified": False, "fingerprint": fp,
                    "reason": "no NIMC channel configured — enrolment continues (fail-soft)"}
        try:
            res = self.verifier(n)
        except Exception as e:
            if fp in self.cache:
                c = self.cache[fp]
                return {"state": "verified-stale" if c["verified"] else "unverified",
                        "verified": c["verified"], "fingerprint": fp, "fresh": False,
                        "reason": f"NIMC unreachable, using last known: {e}"}
            return {"state": "unverified", "verified": False, "fingerprint": fp,
                    "reason": f"NIMC unreachable: {e}"}
        ok = bool(res.get("verified"))
        self.cache[fp] = {"verified": ok, "at": self.now()}
        return {"state": "verified" if ok else "rejected", "verified": ok,
                "fingerprint": fp, "fresh": True,
                **({"name_match": res["name_match"]} if "name_match" in res else {})}

def main(argv):
    doc = json.load(sys.stdin)
    ad = NinAdapter(verifier=None)
    print(json.dumps(ad.verify(doc.get("nin", ""))))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
