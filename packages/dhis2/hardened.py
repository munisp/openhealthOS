"""hardened.py — making the DHIS2 export path production-honest.

dhis2map.py converts platform aggregates to dataValueSets and appends
them to a spool file for the monthly USB run. This module hardens what
happens after — the part where a number either reaches the ministry or
honestly says why not:

1. SPOOL INTEGRITY — every spooled line carries its sha256; replay
   verifies each line and refuses the whole file on the first tampered
   line. A spool that sat on a USB stick for a month must prove itself
   before the ministry sees it.

2. IMPORT SUMMARY PARSING — DHIS2 answers /api/dataValueSets with
   importCount {imported, updated, ignored, deleted} and conflicts[].
   Conflicts are the scheme saying "I rejected some of your numbers" —
   they must surface, never be swallowed. ignored > 0 without conflicts
   is a warning, reported as such.

3. DUPLICATE RESEND GUARD — the same (orgUnit, period, payload-hash)
   pushed twice is a duplicate, returned without re-pushing. A CHANGED
   payload for an already-pushed (orgUnit, period) is a supersede —
   pushed, and the manifest records that the ministry's figure changed.

4. MAPPING COVERAGE — a report of which facilities and measures have no
   DHIS2 mapping, so a state knows its blind spots before the export,
   not after the ministry calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile


class HardenedError(Exception):
    pass


# ------------------------------------------------------------- 1. spool

def spool_line(payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True)
    return json.dumps({"sha256": hashlib.sha256(body.encode()).hexdigest(),
                       "payload": payload}, sort_keys=True)


def spool_append(spool_path: str, payload: dict):
    os.makedirs(os.path.dirname(spool_path) or ".", exist_ok=True)
    with open(spool_path, "a") as f:
        f.write(spool_line(payload) + "\n")


def replay_spool(spool_path: str, push_fn) -> dict:
    """Replay a spool in order through push_fn(payload)->summary.
    First tampered or malformed line aborts the replay: the ministry
    gets a complete honest export or none, never a silently-gapped one.
    Returns {pushed, results}."""
    results, pushed = [], 0
    with open(spool_path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                body = json.dumps(rec["payload"], sort_keys=True)
            except (json.JSONDecodeError, KeyError) as exc:
                raise HardenedError(
                    f"spool line {n} is malformed — replay aborted; "
                    "a partial month is worse than a late month") from exc
            if hashlib.sha256(body.encode()).hexdigest() != rec.get("sha256"):
                raise HardenedError(
                    f"spool line {n} failed its integrity check — "
                    "replay aborted; quarantine this file")
            results.append(push_fn(rec["payload"]))
            pushed += 1
    return {"pushed": pushed, "results": results}


# ------------------------------------------------- 2. import summaries

def parse_import_summary(resp: dict) -> dict:
    """DHIS2 /api/dataValueSets response -> honest outcome.
    {accepted, updated, ignored, deleted, conflicts, warnings, ok}.
    ok=False when any conflict exists — conflicts mean numbers were
    REJECTED by the ministry system and someone must act."""
    ic = resp.get("importCount", {})
    conflicts = [
        {"object": c.get("object", "?"), "value": c.get("value", "?")}
        for c in resp.get("conflicts", [])
    ]
    ignored = ic.get("ignored", 0)
    warnings = []
    if ignored and not conflicts:
        warnings.append(
            f"{ignored} values ignored without a stated conflict — "
            "usually a period lock or org-unit mismatch; verify in DHIS2")
    return {
        "accepted": ic.get("imported", 0),
        "updated": ic.get("updated", 0),
        "ignored": ignored,
        "deleted": ic.get("deleted", 0),
        "conflicts": conflicts,
        "warnings": warnings,
        "ok": not conflicts,
    }


# ------------------------------------------------ 3. duplicate resend

class ResendGuard:
    """Manifest of what was pushed: (orgUnit, period) -> payload hash.
    duplicate  — identical payload re-pushed: skip, say so.
    supersede  — changed payload for a pushed (ou, period): push, record.
    new        — never seen: push, record."""

    def __init__(self, manifest_path: str):
        self.path = manifest_path
        self.sent = {}
        if os.path.exists(manifest_path):
            with open(manifest_path) as f:
                self.sent = json.load(f)

    def _save(self):
        d = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=d or ".", prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump(self.sent, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def check(self, payload: dict) -> str:
        """Classify without recording. Keys by (orgUnit, period) pairs
        present in the payload's dataValues."""
        keys = sorted({(v.get("orgUnit", ""), v.get("period", ""))
                       for v in payload.get("dataValues", [])})
        if not keys:
            raise HardenedError("payload carries no dataValues")
        h = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()
        statuses = set()
        for ou, period in keys:
            prior = self.sent.get(f"{ou}|{period}")
            if prior is None:
                statuses.add("new")
            elif prior == h:
                statuses.add("duplicate")
            else:
                statuses.add("supersede")
        if statuses == {"duplicate"}:
            return "duplicate"
        if "supersede" in statuses:
            return "supersede"
        return "new"

    def record(self, payload: dict):
        h = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()).hexdigest()
        for v in payload.get("dataValues", []):
            self.sent[f"{v.get('orgUnit','')}|{v.get('period','')}"] = h
        self._save()


# --------------------------------------------------- 4. coverage report

def coverage_report(mapping, expected_facilities: list[str],
                    expected_measures: list[str]) -> dict:
    """Which of the state's facilities/measures cannot yet export.
    Unmapped means invisible to the ministry — this is the list a state
    HMIS officer works through, not an error we paper over."""
    missing_ou = [f for f in expected_facilities
                  if f not in mapping.org_units]
    missing_de = [m for m in expected_measures
                  if not any(k == m or k.startswith(m + "_")
                             for k in mapping.data_elements)]
    return {
        "facilities_mapped": len(expected_facilities) - len(missing_ou),
        "facilities_total": len(expected_facilities),
        "facilities_unmapped": missing_ou,
        "measures_mapped": len(expected_measures) - len(missing_de),
        "measures_total": len(expected_measures),
        "measures_unmapped": missing_de,
        "ready": not missing_ou and not missing_de,
    }
