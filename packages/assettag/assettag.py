"""OpenHealth OS — assettag: signed QR/barcode identity for hospital assets (ADR-0052).

Every physical asset — MRI, ultrasound, oxygen concentrator, fridge,
ambulance, vaccine carrier — gets a printed tag carrying a compact,
HMAC-signed payload. Staff scan with the PWA camera (BarcodeDetector);
the equipment-service verifies the signature before acting, so a
photocopied or guessed tag cannot move custody.

Payload format (compact, printable at 25mm, QR version ≤ 6):

    OHAST1.<facility>.<asset>.<issued_unix>.<hmac8>

    OHAST1          format version
    facility        registry facility id (no dots)
    asset           asset id (no dots)
    issued_unix     issue timestamp — rotation and staleness checks
    hmac8           first 8 bytes of HMAC-SHA256(secret, body), hex

Why HMAC and not a database lookup: the scanner must verify OFFLINE
(edge doctrine) — the signature travels with the tag; the audit
reconciles when the link returns.

CLI:
    python3 assettag.py mint FACILITY ASSET --secret-env OH_ASSET_SECRET
    python3 assettag.py verify OHAST1.KDH-01.MRI-03.1761000000.ab12cd34 --secret-env ...
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import sys
import time

PREFIX = "OHAST1"
MAX_AGE_DAYS = 365 * 5  # tags older than this demand re-issue (wear, loss)


class TagError(ValueError):
    pass


def _secret(env_name: str) -> bytes:
    s = os.environ.get(env_name, "")
    if len(s) < 16:
        raise TagError(f"{env_name} must be set (>=16 chars)")
    return s.encode()


def _sig(secret: bytes, body: str) -> str:
    return hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()[:16]


def mint(facility: str, asset: str, secret: bytes, issued: int | None = None) -> str:
    if "." in facility or "." in asset or not facility or not asset:
        raise TagError("facility/asset must be non-empty and dot-free")
    issued = int(issued if issued is not None else time.time())
    body = f"{PREFIX}.{facility}.{asset}.{issued}"
    return f"{body}.{_sig(secret, body)}"


def verify(tag: str, secret: bytes, now: int | None = None) -> dict:
    """Parse + verify. Raises TagError on any anomaly; on success returns
    {facility, asset, issued, stale}. Stale is informational (re-print
    the tag), not a rejection — a worn tag shouldn't strand an asset."""
    parts = tag.strip().split(".")
    if len(parts) != 5 or parts[0] != PREFIX:
        raise TagError("malformed tag (want OHAST1.<facility>.<asset>.<issued>.<sig>)")
    body = ".".join(parts[:4])
    if not hmac.compare_digest(_sig(secret, body), parts[4]):
        raise TagError("bad signature — tag rejected")
    try:
        issued = int(parts[3])
    except ValueError:
        raise TagError("bad issue timestamp")
    now = int(now if now is not None else time.time())
    if issued > now + 300:
        raise TagError("issue timestamp in the future")
    return {"facility": parts[1], "asset": parts[2], "issued": issued,
            "stale": (now - issued) > MAX_AGE_DAYS * 86400}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="signed asset tags")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("facility")
    m.add_argument("asset")
    v = sub.add_parser("verify")
    v.add_argument("tag")
    for p in (m, v):
        p.add_argument("--secret-env", default="OH_ASSET_SECRET")
    args = ap.parse_args(argv)
    secret = _secret(args.secret_env)
    if args.cmd == "mint":
        print(mint(args.facility, args.asset, secret))
        return 0
    try:
        out = verify(args.tag, secret)
    except TagError as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 1
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
