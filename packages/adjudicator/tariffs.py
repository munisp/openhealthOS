#!/usr/bin/env python3
"""Tariff bundles (ADR-0015) — state-uploadable, versioned, validated.

The adjudicator used to hard-code its tariff schedule; the gap was that a
state scheme could not upload its own negotiated schedule without a code
change. A tariff bundle is a versioned JSON document:

    {
      "bundle_id": "ng-default-2026",
      "version": 1,
      "state": "NG",                    # "NG" = national default, or e.g. "KD"
      "effective_from": "2026-01-01",   # ISO date; not valid before this
      "codes": { "MAL-RDT": {"amount": 1500, "name": "Malaria RDT"} },
      "freq_caps": { "MAL-RDT": 2 }     # optional, max units / 14 days
    }

Selection: OH_TARIFF_BUNDLE points at a JSON file; otherwise the built-in
national default ships. Every adjudication stamps the bundle id+version so
a payment can always be traced to the exact schedule that priced it.
Validation is total: a malformed bundle fails loudly at load, never
silently prices a claim wrong.
"""
from __future__ import annotations

import json
import os
from datetime import date

REQUIRED_KEYS = ("bundle_id", "version", "state", "effective_from", "codes")


class BundleError(ValueError):
    pass


def validate_bundle(b: dict, today: date | None = None) -> list[str]:
    """Return a list of validation errors ([] = valid). Total, not first-error."""
    errs: list[str] = []
    for k in REQUIRED_KEYS:
        if k not in b:
            errs.append(f"missing required key: {k}")
    if errs:
        return errs
    if not isinstance(b["bundle_id"], str) or not b["bundle_id"].strip():
        errs.append("bundle_id must be a non-empty string")
    if not isinstance(b["version"], int) or b["version"] < 1:
        errs.append("version must be a positive integer")
    if not isinstance(b["state"], str) or not b["state"].strip():
        errs.append("state must be a non-empty string")
    try:
        eff = date.fromisoformat(str(b["effective_from"]))
        if today and eff > today:
            errs.append(f"effective_from {eff} is in the future")
    except ValueError:
        errs.append(f"effective_from {b['effective_from']!r} is not an ISO date")
    codes = b.get("codes")
    if not isinstance(codes, dict) or not codes:
        errs.append("codes must be a non-empty object")
    else:
        for code, entry in codes.items():
            if not isinstance(entry, dict):
                errs.append(f"codes.{code}: must be an object")
                continue
            amount = entry.get("amount")
            if not isinstance(amount, (int, float)) or amount <= 0:
                errs.append(f"codes.{code}.amount must be a positive number")
            if "name" not in entry:
                errs.append(f"codes.{code}.name is required (auditable line items)")
    caps = b.get("freq_caps", {})
    if not isinstance(caps, dict):
        errs.append("freq_caps must be an object")
    else:
        for code, cap in caps.items():
            if isinstance(codes, dict) and code not in codes:
                errs.append(f"freq_caps.{code}: not a tariffed code")
            if not isinstance(cap, int) or cap < 1:
                errs.append(f"freq_caps.{code} must be a positive integer")
    return errs


def load_bundle(path: str, today: date | None = None) -> dict:
    """Load and validate a bundle from a JSON file. Raises BundleError."""
    try:
        with open(path, encoding="utf-8") as f:
            b = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise BundleError(f"cannot read tariff bundle {path}: {e}") from e
    errs = validate_bundle(b, today=today)
    if errs:
        raise BundleError(f"invalid tariff bundle {path}: " + "; ".join(errs))
    return b


def tariff_map(b: dict) -> dict[str, int]:
    return {code: int(entry["amount"]) for code, entry in b["codes"].items()}


def freq_caps(b: dict) -> dict[str, int]:
    return dict(b.get("freq_caps", {}))


_DEFAULT = os.path.join(os.path.dirname(__file__), "bundles", "ng-default-2026.json")


def active_bundle() -> dict:
    """The bundle in force: OH_TARIFF_BUNDLE override, else the built-in
    national default. Loaded fresh per call — a state upload takes effect
    without a redeploy."""
    return load_bundle(os.environ.get("OH_TARIFF_BUNDLE", _DEFAULT))
