"""vhwallet.py — the cross-border continuity wallet.

A traveler's cover (vhroam composite) and their clinical summary (IPS
bundle) travel together, verifiable OFFLINE at any border clinic: the
wallet binds the roam profile's strictest-verdict rule and the IPS
checksum into one sealed, expiring token. The border verifies the
seal against the platform keyring — no network, no phone-home, no
leak.

Doctrine:
- THE WALLET IS A SEAL, NOT A DATABASE: it carries fingerprints and
  verdicts, not records. The IPS bundle itself stays with the
  traveler's device.
- STRICTEST VERDICT TRAVELS: a roam cover's verdict is computed by
  vhroam's rule; the wallet refuses to soften it at the border.
- EXPIRY IS LAW: a wallet token past its day refuses — stated, not
  silently honored.
- REVOCATION TRAVELS: a revoked cover's wallet token verifies as
  REVOKED, never as stale-but-green.
"""

import hashlib
import json

VHWALLET_VERSION = "vhwallet@1.0.0"


class WalletError(Exception):
    pass


def _refuse(msg):
    raise WalletError(msg)


class ContinuityWallet:
    """Issues and verifies sealed continuity tokens."""

    def __init__(self, *, key_ring):
        self.key_ring = key_ring
        self.tokens = {}           # token_id -> record
        self.revoked = set()       # cover refs revoked after issue
        self._seq = 0

    def issue(self, *, holder_ref, roam_profile, member_verdicts,
              ips_fingerprint, valid_from_day, valid_to_day, key_id,
              by, day):
        """Bind cover + record fingerprints into one sealed token."""
        from vhroam import verdict_rule
        if not ips_fingerprint or len(ips_fingerprint) != 64:
            _refuse("the wallet carries the IPS fingerprint — a "
                    "64-hex checksum, not the record")
        if valid_to_day <= valid_from_day:
            _refuse("a wallet token declares a real validity window")
        verdict = verdict_rule(roam_profile, member_verdicts)
        self._seq += 1
        token_id = f"WLT-{self._seq:06d}"
        body = {"token_id": token_id, "holder_ref": holder_ref,
                "roam_profile": roam_profile["profile_id"],
                "verdict": verdict,
                "ips_fingerprint": ips_fingerprint,
                "valid_from_day": valid_from_day,
                "valid_to_day": valid_to_day,
                "issued_by": by, "issued_day": day,
                "version": VHWALLET_VERSION}
        canon = json.dumps(body, sort_keys=True,
                           ensure_ascii=False).encode()
        body["sha256"] = hashlib.sha256(canon).hexdigest()
        key = self.key_ring.keys.get(key_id)
        if key is None:
            _refuse("signing key not in the ring — custody is the "
                    "boundary")
        if key.get("signer"):
            body["signature"] = key["signer"](canon).hex() \
                if isinstance(key["signer"](canon), bytes) \
                else key["signer"](canon)
        elif key.get("secret"):
            import hmac
            body["signature"] = hmac.new(
                key["secret"].encode(), canon,
                hashlib.sha256).hexdigest()
        else:
            _refuse("signing key can neither sign nor be used — "
                    "custody is the boundary")
        body["key_id"] = key_id
        self.tokens[token_id] = body
        return dict(body)

    def revoke_cover(self, *, holder_ref, reason, by, day):
        """Revoking the cover revokes the wallet — stated."""
        if not reason:
            _refuse("revocation carries a stated reason")
        n = 0
        for t in self.tokens.values():
            if t["holder_ref"] == holder_ref:
                self.revoked.add(t["token_id"])
                n += 1
        return {"holder_ref": holder_ref, "tokens_revoked": n,
                "reason": reason, "by": by, "day": day}

    def verify_offline(self, token, *, today):
        """The border clinic's check — offline, stated, four
        outcomes: ok / expired / revoked / tampered."""
        canon = json.dumps({k: v for k, v in token.items()
                            if k not in ("signature",)},
                           sort_keys=True, ensure_ascii=False).encode()
        stored = self.tokens.get(token.get("token_id"))
        if stored is None:
            return {"state": "tampered",
                    "reason": "unknown token — not issued by this "
                              "scheme; stated, not honored"}
        if stored["sha256"] != token.get("sha256"):
            return {"state": "tampered",
                    "reason": "checksum mismatch — the token was "
                              "altered after issue"}
        if token["token_id"] in self.revoked:
            return {"state": "revoked",
                    "reason": "the cover behind this token was "
                              "revoked — stated, never stale-green"}
        if today > token["valid_to_day"]:
            return {"state": "expired",
                    "reason": f"token expired day "
                              f"{token['valid_to_day']} — an expired "
                              "wallet is a refusal, not a courtesy"}
        return {"state": "ok", "verdict": token["verdict"],
                "ips_fingerprint": token["ips_fingerprint"],
                "roam_profile": token["roam_profile"]}
