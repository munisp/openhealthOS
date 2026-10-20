#!/usr/bin/env python3
"""Encounter linkage — the anti-ghost-claim gate (ADR-0018).

The single most expensive facility fraud is the ghost claim: a bill for
care that never happened, against a member who was never there. The
structural answer is linkage: money follows encounters, and encounters
follow presence. A claim that cannot point at a platform encounter
matching member + facility + date is not necessarily fraud — the edge
syncs late, paper referrals happen — so the gate PENDS, never denies:
human review with the evidence laid out.

The encounter store is a seam:
  ENCOUNTER_FILE — JSONL, one encounter per line:
    {"encounter_id": "ENC-1", "patient_id": "NG-0001",
     "facility_id": "NG-KD-0014", "date": "2026-10-19",
     "presence": "biometric" | "card" | "self"}
  unset — the dev fixture below.

`presence` is the strength of proof that a body was at the desk:
biometric (1:1 verify at point of care) > card (KYC'd member card) >
self (name given). Presence never blocks care; it prices trust — a
facility whose claims all ride on self-presence earns more review
attention than one verifying fingerprints, and the integrity scorecard
(claims-orchestrator /integrity) says exactly that.
"""
from __future__ import annotations

import json
import os

# Dev fixture — production sets ENCOUNTER_FILE and the SHR writes one
# line per encounter as it is created (edge first, synced state-side).
FIXTURE_ENCOUNTERS = [
    {"encounter_id": "ENC-0001", "patient_id": "NG-0001",
     "facility_id": "NG-KD-0014", "date": "2026-10-12", "presence": "biometric"},
    {"encounter_id": "ENC-0002", "patient_id": "NG-0002",
     "facility_id": "NG-KD-0014", "date": "2026-10-15", "presence": "card"},
    {"encounter_id": "ENC-0003", "patient_id": "NG-0001",
     "facility_id": "PHC-007", "date": "2026-10-01", "presence": "card"},
]

# qr-day (ADR-0055): the patient scanned the facility's daily presence QR
# with their own authenticated phone — device-bound, day-rotating proof,
# ranked with card presence.
PRESENCE_RANK = {"self": 0, "card": 1, "qr-day": 1, "biometric": 2}


# In-process cache keyed by (path, mtime): production adjudication is
# one-claim-per-subprocess so the cache lives for a single lookup, but
# in-process callers (the million-claim benchmark, tests) would otherwise
# re-read and re-parse the whole encounter log per claim — O(n²) IO.
# mtime is part of the key, so a file that changes mid-run is re-read.
_ENCOUNTER_CACHE: dict[str, tuple[float, list[dict]]] = {}


def load_encounters(path: str | None = None) -> list[dict]:
    """JSONL encounter log; missing file is honest empty, malformed lines
    are skipped (a torn write at power loss must not break adjudication)."""
    path = path or os.environ.get("ENCOUNTER_FILE", "")
    if not path:
        return list(FIXTURE_ENCOUNTERS)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    cached = _ENCOUNTER_CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]  # shared, read-only — callers must not mutate
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if all(k in row for k in ("encounter_id", "patient_id", "facility_id", "date")):
                    out.append(row)
    except OSError:
        return []
    _ENCOUNTER_CACHE.clear()
    _ENCOUNTER_CACHE[path] = (mtime, out)
    _INDEX_CACHE.clear()
    _INDEX_CACHE[id(out)] = {e["encounter_id"]: e for e in out}
    return out  # shared, read-only — callers must not mutate


# encounter_id → row, keyed by the identity of the cached list; rebuilt
# only when the file changes (the cache above is the invalidation point).
_INDEX_CACHE: dict[int, dict[str, dict]] = {}


def _index(encounters: list[dict]) -> dict[str, dict]:
    idx = _INDEX_CACHE.get(id(encounters))
    if idx is None or len(idx) != len(encounters):
        idx = {e["encounter_id"]: e for e in encounters}
        _INDEX_CACHE.clear()
        _INDEX_CACHE[id(encounters)] = idx
    return idx


def check_linkage(claim: dict, encounters: list[dict] | None = None) -> dict:
    """Verify the claim's encounter evidence.

    Returns a dict:
      {"linked": bool, "presence": str|None, "flags": [str, ...]}
    linked=False with no encounter_id is the classic ghost shape;
    linked=False with an id that doesn't match is worse — a forged
    reference, which says someone knew the gate existed.
    """
    indexed = encounters is None
    encounters = encounters if encounters is not None else load_encounters()
    enc_id = claim.get("encounter_id", "")
    member = claim.get("member_id", "")
    facility = claim.get("facility_id") or claim.get("provider_id", "")
    date = claim.get("service_date", "")
    flags: list[str] = []

    if not enc_id:
        flags.append("LINKAGE: no encounter reference — claim cannot prove the care happened")
        return {"linked": False, "presence": None, "flags": flags}

    # Index when we loaded the log ourselves — a linear scan per claim is
    # O(n²) across a national claim stream (the benchmark proved this).
    if indexed:
        match = _index(encounters).get(enc_id)
    else:
        match = next((e for e in encounters if e["encounter_id"] == enc_id), None)
    if match is None:
        flags.append(f"LINKAGE: encounter {enc_id} not on record — forged reference?")
        return {"linked": False, "presence": None, "flags": flags}

    problems = []
    if match["patient_id"] != member:
        problems.append(f"member {member} vs encounter patient {match['patient_id']}")
    if match["facility_id"] != facility:
        problems.append(f"facility {facility} vs encounter facility {match['facility_id']}")
    if match["date"] != date:
        problems.append(f"service date {date} vs encounter date {match['date']}")
    if problems:
        flags.append("LINKAGE: encounter mismatch — " + "; ".join(problems))
        return {"linked": False, "presence": match.get("presence"), "flags": flags}

    presence = match.get("presence") or "self"
    if PRESENCE_RANK.get(presence, 0) < PRESENCE_RANK["card"]:
        flags.append(f"PRESENCE: encounter {enc_id} verified only as '{presence}' — "
                     "review at sampling rate, biometric presence clears this")
    return {"linked": True, "presence": presence, "flags": flags}
