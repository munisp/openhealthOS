#!/usr/bin/env python3
"""shl — SMART Health Links payload for the IPS share path (ADR-0054).

A SMART Health Link lets a patient hand their summary to ANY clinician
with a QR scanner, no federation agreement needed: the QR encodes a URL
(where the encrypted summary lives) + a decryption key that never touches
the server. This module builds and parses the link payload:

  payload = {"url": "https://…/shl/F1/abc", "key": "<b64url 32B>",
             "exp": 1761000000, "flag": "P", "label": "…"}
  link    = "shlink:/" + base64url(json(payload))

Flags (SHL spec): "P" = passcode required, "L" = long-term.
The content file is encrypted with AES-256-GCM by tools/ipsqr (Go stdlib
crypto — Python stdlib has no AEAD, so encryption deliberately lives in
the Go hot path; see shlfile.go). The payload here is spec-shaped; the
file envelope is ours ("OHSHL1") until a counterparty needs full JWE —
documented divergence in ADR-0054.

CLI:
  python3 shl.py mint --url https://e/f/shl/abc --label "IPS — Amina" [--days 30] [--passcode]
  python3 shl.py parse "shlink:/eyJ…"
"""
from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
import time

SHLINK_PREFIX = "shlink:/"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def mint_payload(url: str, label: str, days: int = 30,
                 passcode: bool = False, long_term: bool = False) -> dict:
    """A fresh SHL payload with a newly generated 256-bit content key."""
    if not url.startswith("https://") and not url.startswith("http://"):
        raise ValueError("url must be http(s)")
    flag = ("P" if passcode else "") + ("L" if long_term else "")
    payload = {
        "url": url,
        "key": _b64(secrets.token_bytes(32)),
        "exp": int(time.time()) + days * 86400,
        "label": label,
    }
    if flag:
        payload["flag"] = flag
    return payload


def encode_link(payload: dict) -> str:
    return SHLINK_PREFIX + _b64(json.dumps(payload, separators=(",", ":")).encode())


def parse_link(link: str) -> dict:
    """Parse + sanity-check a shlink: URI. Fail-closed: a link without a
    key or url is not a health link, it's a sticker."""
    if not link.startswith(SHLINK_PREFIX):
        raise ValueError("not a shlink: URI")
    payload = json.loads(_unb64(link[len(SHLINK_PREFIX):]))
    if not payload.get("url") or not payload.get("key"):
        raise ValueError("SHL payload missing url or key")
    key = _unb64(payload["key"])
    if len(key) != 32:
        raise ValueError(f"SHL key must be 32 bytes, got {len(key)}")
    if payload.get("exp", 0) <= int(time.time()):
        payload["_expired"] = True
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="shl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("--url", required=True)
    m.add_argument("--label", required=True)
    m.add_argument("--days", type=int, default=30)
    m.add_argument("--passcode", action="store_true")
    m.add_argument("--long-term", action="store_true")
    p = sub.add_parser("parse")
    p.add_argument("link")
    args = ap.parse_args(argv)
    if args.cmd == "mint":
        payload = mint_payload(args.url, args.label, args.days, args.passcode, args.long_term)
        print(json.dumps({"payload": payload, "link": encode_link(payload)}, indent=2))
        return 0
    print(json.dumps(parse_link(args.link), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
