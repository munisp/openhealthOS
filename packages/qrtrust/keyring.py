#!/usr/bin/env python3
"""qrtrust — shared keyring, revocation and payload-budget doctrine for
every signed QR the platform prints (ADR-0055).

One seam, used by assettag v2, membercard and presenceqr:

- KEYRING: env var holds a JSON map of key-id → secret, e.g.
    OH_QR_KEYS='{"k2025":"…","k2026":"…"}'  +  OH_QR_ACTIVE=k2026
  Old keys stay in the ring so in-field tags keep verifying after
  rotation; only the active key mints. A single plain secret (legacy
  OH_ASSET_SECRET style) degrades to a one-key ring under kid "k0" —
  never to "no key".
- KEY ID IN EVERY PAYLOAD: v2 payloads carry <kid> as the second field,
  so a scanner knows which key to try — no trial-and-error oracle.
- REVOCATION: a JSONL list of revoked payload bodies (leaked sheet of
  tags, decommissioned asset). Revocation is a hard reject, distinct
  from a bad signature.
- PAYLOAD BUDGET: QRs on $100-phone cameras need low density. Budget is
  800 bytes target / 2000 bytes hard ceiling, EC level Q. minters call
  check_budget() and refuse beyond the ceiling.

All HMAC-SHA256, stdlib only, offline-verifiable — the edge doctrine.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os

BUDGET_TARGET_BYTES = 800
BUDGET_MAX_BYTES = 2000
EC_LEVEL = "Q"  # 25% damage tolerance — cracked screens, dirty printouts


class TrustError(ValueError):
    pass


class RevokedError(TrustError):
    pass


def load_keyring(env_name: str = "OH_QR_KEYS", active_env: str = "OH_QR_ACTIVE") -> tuple[dict[str, bytes], str]:
    """Returns ({kid: secret}, active_kid). Fail-closed: no usable key
    raises; a ring where the active kid is missing raises."""
    raw = os.environ.get(env_name, "")
    ring: dict[str, bytes] = {}
    if raw.strip().startswith("{"):
        try:
            for kid, secret in json.loads(raw).items():
                if len(str(secret)) >= 16 and kid and "." not in kid:
                    ring[kid] = str(secret).encode()
        except json.JSONDecodeError:
            raise TrustError(f"{env_name} is not valid JSON")
    elif len(raw) >= 16:
        ring["k0"] = raw.encode()  # legacy single-secret degradation
    if not ring:
        raise TrustError(f"{env_name} holds no usable key (>=16 chars) — signing disabled")
    active = os.environ.get(active_env, "") or sorted(ring)[-1]
    if active not in ring:
        raise TrustError(f"active key {active!r} not in keyring")
    return ring, active


def sign(secret: bytes, body: str) -> str:
    return hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()[:16]


def verify_with_ring(tag: str, ring: dict[str, bytes], expect_prefix: str,
                     fields: int, revoked: set[str] | None = None) -> list[str]:
    """Verify a v2 tag '<prefix>.<kid>.<f1>….<sig>'. Returns the payload
    fields between kid and sig. Raises TrustError/RevokedError."""
    parts = tag.strip().split(".")
    if len(parts) != fields + 3 or parts[0] != expect_prefix:
        raise TrustError(f"malformed tag (want {expect_prefix}.<kid>.<{fields} fields>.<sig>)")
    kid = parts[1]
    secret = ring.get(kid)
    if secret is None:
        raise TrustError(f"unknown key id {kid!r} — key retired or tag forged")
    body = ".".join(parts[:-1])
    if not hmac.compare_digest(sign(secret, body), parts[-1]):
        raise TrustError("bad signature — tag rejected")
    if revoked and body in revoked:
        raise RevokedError("tag revoked — key sheet leaked or asset decommissioned")
    return parts[2:-1]


def load_revocations(path: str) -> set[str]:
    """JSONL revocation list: {"body": "<prefix>.<kid>.…"} per line.
    Missing file is an honest empty list; malformed lines are skipped."""
    out: set[str] = set()
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("body"):
                    out.add(row["body"])
    except FileNotFoundError:
        pass
    return out


def check_budget(payload: str) -> dict:
    """QR density guard. Beyond the ceiling the code won't scan reliably
    on low-end cameras — refuse, don't print a decoration."""
    n = len(payload.encode())
    if n > BUDGET_MAX_BYTES:
        raise TrustError(f"payload {n}B exceeds QR budget {BUDGET_MAX_BYTES}B — shorten or switch to a link-style QR")
    # byte-mode capacity at EC-Q ≈ 32B×version for mid versions; estimate
    est_version = max(1, min(40, round((n / 32) ** 0.5) + 1))
    return {"bytes": n, "ec_level": EC_LEVEL, "version_est": est_version,
            "within_target": n <= BUDGET_TARGET_BYTES}
