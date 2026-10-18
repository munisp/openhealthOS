#!/usr/bin/env python3
"""Enrolment store — the source of truth for "is this person covered?"
(ADR-0017).

The adjudicator's MEMBERS fixture grows up into a real store: a plain
JSON document at MEMBERS_FILE (edge-deployable, syncable, auditable —
an enrolment is an event a state office can diff). The shape is
deliberately the fixture's shape, so every existing consumer
(adjudication, preauth, eligibility check, USSD) reads it unchanged.

Rules enforced here:
  * A member_id is issued once; re-enrolment updates, never duplicates.
  * Scheme must be registered to a payer (payers.py) — an unknown scheme
    is refused at enrolment, not discovered at claim time.
  * Suspension is a state change with a reason, never a delete —
    coverage history is an audit trail.
  * biometric_required flags members whose scheme demands a biometric
    match before high-value care (anti card-sharing); the eligibility
    check surfaces it, point-of-care enforcement lives in the registry.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date

from payers import DEFAULT_PAYERS

REQUIRED_FIELDS = ("member_id", "name", "scheme", "valid_until")

KNOWN_SCHEMES = {s for p in DEFAULT_PAYERS for s in p.schemes}


class EnrolmentError(ValueError):
    pass


def load_members(path: str | None = None) -> dict:
    """Load the member registry; missing file = empty registry (a fresh
    edge node has enrolled nobody yet — that is the truth, not an error)."""
    path = path or os.environ.get("MEMBERS_FILE", "")
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise EnrolmentError("members file must be a JSON object keyed by member_id")
    return data


def save_members(members: dict, path: str | None = None) -> None:
    """Atomic write: enrolment data never survives a torn write."""
    path = path or os.environ.get("MEMBERS_FILE", "")
    if not path:
        raise EnrolmentError("MEMBERS_FILE unset — nowhere durable to enrol")
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".members-", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(members, f, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _valid_date(s: str) -> bool:
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def validate(record: dict) -> dict:
    """Normalise + validate one enrolment record. Loud, never partial."""
    for f in REQUIRED_FIELDS:
        if not record.get(f):
            raise EnrolmentError(f"missing required field: {f}")
    if record["scheme"] not in KNOWN_SCHEMES:
        raise EnrolmentError(f"unknown scheme {record['scheme']!r} — register the payer first")
    if not _valid_date(record["valid_until"]):
        raise EnrolmentError("valid_until must be ISO date (YYYY-MM-DD)")
    out = {
        "name": str(record["name"]),
        "scheme": record["scheme"],
        "active": bool(record.get("active", True)),
        "valid_until": record["valid_until"],
        "copay_waiver": bool(record.get("copay_waiver", False)),
        "biometric_required": bool(record.get("biometric_required", False)),
    }
    if record.get("nin"):
        out["nin"] = str(record["nin"])
    if record.get("suspend_reason"):
        out["suspend_reason"] = str(record["suspend_reason"])
    return out


def enrol(record: dict, path: str | None = None) -> dict:
    """Upsert one member. member_id is issued once — re-enrolment is an
    update (renewal, scheme change), never a second identity."""
    members = load_members(path)
    mid = record["member_id"]
    members[mid] = validate(record)
    save_members(members, path)
    return {"member_id": mid, **members[mid]}


def suspend(member_id: str, reason: str, path: str | None = None) -> dict:
    """Suspend coverage with a reason. Never a delete — history stays."""
    if not reason:
        raise EnrolmentError("suspension requires a reason — silent coverage changes are fraud cover")
    members = load_members(path)
    if member_id not in members:
        raise EnrolmentError(f"unknown member {member_id!r}")
    members[member_id]["active"] = False
    members[member_id]["suspend_reason"] = reason
    save_members(members, path)
    return {"member_id": member_id, **members[member_id]}
