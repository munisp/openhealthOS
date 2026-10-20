#!/usr/bin/env python3
"""membercard — signed offline member-identity QR (ADR-0055).

A scheme member card today is paper + hope: eligibility checks need the
network, and a typed-in member id proves nothing. This package mints a
wallet-sized QR that a CHW's PWA can verify with ZERO connectivity:

    OHMEM1.<kid>.<member>.<scheme>.<valid_yyyymmdd>.<hmac>

Verify answers three questions offline: is this card authentic (HMAC),
whose is it (member id, scheme), and is it in date (valid_until). What it
deliberately does NOT answer: benefit details (those live in the benefits
package — the card says who you are, not what you're owed) and revocation
of individual cards (card bodies CAN be revoked via the qrtrust
revocation list — a leaked print sheet is the attack, and it's covered).

CLI:
  python3 membercard.py mint NG-0001 BHCPF-VUL --until 2027-03-31
  python3 membercard.py verify OHMEM1.k2026.NG-0001.BHCPF-VUL.20270331.ab12…
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from keyring_shared import (  # local shim re-exporting qrtrust
    PREFIX, RevokedError, TrustError, check_budget, load_keyring,
    load_revocations, sign, verify_with_ring,
)

FIELDS = 3  # member, scheme, valid_yyyymmdd


def mint(member: str, scheme: str, valid_until: str,
         env: str = "OH_QR_KEYS") -> str:
    if not member or not scheme or "." in member or "." in scheme:
        raise TrustError("member/scheme must be non-empty and dot-free")
    datetime.strptime(valid_until, "%Y-%m-%d")  # raises on bad date
    ring, active = load_keyring(env)
    body = f"{PREFIX}.{active}.{member}.{scheme}.{valid_until.replace('-', '')}"
    tag = f"{body}.{sign(ring[active], body)}"
    check_budget(tag)
    return tag


def verify(tag: str, now: int | None = None, revoked_path: str = "",
           env: str = "OH_QR_KEYS") -> dict:
    """Offline eligibility proof. Raises on forgery/revocation; returns
    {member, scheme, valid_until, expired} — expired is informational
    (renew the card), never a silent pass."""
    ring, _ = load_keyring(env)
    revoked = load_revocations(revoked_path) if revoked_path else None
    member, scheme, yyyymmdd = verify_with_ring(tag, ring, PREFIX, FIELDS, revoked)
    valid_until = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"
    expiry = int(datetime.strptime(valid_until, "%Y-%m-%d").timestamp())
    now = int(now if now is not None else time.time())
    return {"member": member, "scheme": scheme, "valid_until": valid_until,
            "expired": now > expiry + 86400}  # same-day grace


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="membercard")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("member")
    m.add_argument("scheme")
    m.add_argument("--until", required=True, help="YYYY-MM-DD")
    v = sub.add_parser("verify")
    v.add_argument("tag")
    v.add_argument("--revoked", default="")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "mint":
            print(mint(args.member, args.scheme, args.until))
            return 0
        print(json.dumps(verify(args.tag, revoked_path=args.revoked)))
        return 0
    except (TrustError, RevokedError) as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
