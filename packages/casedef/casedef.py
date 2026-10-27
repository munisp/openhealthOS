"""casedef.py — versioned case definitions with push-down expiry.

Mid-outbreak, the case definition changes (v1 → v2 → v3 as the epidemio-
logy sharpens). The failure mode is a facility screening against last
month's definition because the PDF never reached them. This module makes
the definition a versioned, dated artifact the screening flow MUST pin:

  - every screening record carries the definition version used;
  - a definition has a validity window (superseded_at); screening with a
    superseded definition REFUSES with the current version named — sync
    first, screen second;
  - adoption is auditable: how many screenings ran under each version,
    and which facilities are still on stale versions — the audit NCDC's
    IMS asks for on day 3 and gets on day 30.

Doctrine: a case definition is a clinical protocol — it rides
packreview's governance (author cannot self-approve) before activation.
"""
from __future__ import annotations

import hashlib
import json


class CaseDefError(Exception):
    pass


def _fp(d: dict) -> str:
    body = {k: v for k, v in d.items() if k != "superseded_at"}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


class CaseDefRegistry:
    def __init__(self):
        self.defs = {}       # name -> [versions], newest last
        self.screenings = []  # {def_version, facility, day, disposition}

    def activate(self, name: str, definition: dict, effective_day: str,
                 author: str) -> dict:
        """Publish a new version. Supersedes the prior one (which keeps
        its record — history is never erased)."""
        required = ("version", "suspect_criteria", "confirmed_criteria")
        missing = [f for f in required if f not in definition]
        if missing:
            raise CaseDefError(f"definition missing: {', '.join(missing)}")
        versions = self.defs.setdefault(name, [])
        if any(v["definition"]["version"] == definition["version"]
               for v in versions):
            raise CaseDefError(
                f"version {definition['version']} already registered — "
                "a definition never mutates; publish a new version")
        if versions and versions[-1]["superseded_at"] is None:
            versions[-1]["superseded_at"] = effective_day
        rec = {"name": name, "definition": definition, "author": author,
               "effective_day": effective_day, "superseded_at": None,
               "fingerprint": _fp(definition)}
        versions.append(rec)
        return rec

    def current(self, name: str) -> dict:
        versions = self.defs.get(name)
        if not versions:
            raise CaseDefError(f"no case definition registered: {name}")
        return versions[-1]

    # ------------------------------------------------------------ screening

    def screen(self, name: str, def_version: str, answers: dict,
               facility: str, day: str) -> dict:
        """Evaluate a suspect against a PINNED version. Stale version ->
        refuse, naming the current one. The refusal is the feature."""
        versions = self.defs.get(name)
        if not versions:
            raise CaseDefError(f"no case definition registered: {name}")
        rec = next((v for v in versions
                    if v["definition"]["version"] == def_version), None)
        if rec is None:
            raise CaseDefError(f"unknown version {def_version} for {name}")
        cur = versions[-1]
        if rec["superseded_at"] is not None:
            raise CaseDefError(
                f"definition {def_version} was superseded on "
                f"{rec['superseded_at']} — current is "
                f"{cur['definition']['version']}; sync before screening")
        criteria = rec["definition"]["suspect_criteria"]
        matched = [c for c, needed in criteria.items()
                   if answers.get(c) == needed]
        is_suspect = len(matched) == len(criteria)
        record = {"def_version": def_version, "facility": facility,
                  "day": day,
                  "disposition": "suspect" if is_suspect else "not-a-suspect",
                  "criteria_matched": matched}
        self.screenings.append(record)
        return record

    # -------------------------------------------------------------- audit

    def adoption_audit(self, name: str) -> dict:
        """Who screened under what — the stale-facility list the IMS
        actually needs."""
        cur = self.current(name)["definition"]["version"]
        by_version = {}
        stale_facilities = set()
        for s in self.screenings:
            by_version[s["def_version"]] = by_version.get(s["def_version"], 0) + 1
            if s["def_version"] != cur:
                stale_facilities.add(s["facility"])
        return {"current_version": cur,
                "screenings_by_version": by_version,
                "stale_facilities": sorted(stale_facilities),
                # note: stale_facilities is inherently historical — a
                # facility appears here if it EVER screened under an old
                # version; that is the audit trail, not an accusation
                "total_screenings": len(self.screenings)}
