#!/usr/bin/env python3
"""presenceqr — facility-day presence QR (ADR-0055).

The linkage gate (ADR-0018) ranks presence proof: biometric > card >
self. Biometric hardware is rare at PHCs; cards get shared. The cheap
middle rung: each facility prints a DAILY QR for the front desk. The
patient's own phone scans it on arrival — proof that this device (the
one logged into the patient app) was physically at this facility today.

    OHPRS1.<kid>.<facility>.<yyyymmdd>.<hmac>

Anti-replay properties: the code rotates daily (a photographed Monday
code is worthless Tuesday); the scan event binds patient + facility +
date + device-claimed identity; and the PWA captures coarse GPS at scan
time when available — a scan 80km from the facility is flagged by
scan forensics, not trusted blindly.

Presence events feed the encounter store as presence="qr-day" — ranked
with card presence (>= card clears the sampling-review threshold in the
adjudicator's PRESENCE_RANK).

CLI:
  python3 presenceqr.py mint KDH-01 --date 2026-10-20
  python3 presenceqr.py scan OHPRS1.k2026.KDH-01.20261020.ab12… --patient NG-0001
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime

from keyring_shared import (PREFIX, RevokedError, TrustError, check_budget,
                            load_keyring, load_revocations, sign,
                            verify_with_ring)

FIELDS = 2  # facility, yyyymmdd


def mint(facility: str, day: str, env: str = "OH_QR_KEYS") -> str:
    if not facility or "." in facility:
        raise TrustError("facility must be non-empty and dot-free")
    datetime.strptime(day, "%Y-%m-%d")
    ring, active = load_keyring(env)
    body = f"{PREFIX}.{active}.{facility}.{day.replace('-', '')}"
    tag = f"{body}.{sign(ring[active], body)}"
    check_budget(tag)
    return tag


def scan(tag: str, patient: str, now: int | None = None,
         revoked_path: str = "", env: str = "OH_QR_KEYS") -> dict:
    """Verify a scanned day-code and emit the presence event for the
    encounter store. Same-day only: a code from another date is a replay,
    not a presence proof."""
    if not patient:
        raise TrustError("patient id required — presence binds a person")
    ring, _ = load_keyring(env)
    revoked = load_revocations(revoked_path) if revoked_path else None
    facility, yyyymmdd = verify_with_ring(tag, ring, PREFIX, FIELDS, revoked)
    code_date = datetime.strptime(yyyymmdd, "%Y%m%d").date()
    today = datetime.utcfromtimestamp(now if now is not None else time.time()).date()
    if code_date != today:
        raise TrustError(f"day-code is for {code_date}, today is {today} — replay rejected")
    return {
        "patient_id": patient, "facility_id": facility,
        "date": today.isoformat(), "presence": "qr-day",
        "at": int(now if now is not None else time.time()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="presenceqr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mint")
    m.add_argument("facility")
    m.add_argument("--date", required=True)
    s = sub.add_parser("scan")
    s.add_argument("tag")
    s.add_argument("--patient", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "mint":
            print(mint(args.facility, args.date))
            return 0
        print(json.dumps(scan(args.tag, args.patient)))
        return 0
    except (TrustError, RevokedError) as e:
        print(f"REJECTED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
