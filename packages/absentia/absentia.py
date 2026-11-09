"""absentia.py — absence claims with a stated search set (ADR-0061).

last-ehr's sharpest rule: the agent must be built so it CANNOT report
an absence it never checked for. "No allergies on record" is a
different sentence depending on whether the allergy store was queried
or merely not mentioned — and a system that conflates the two turns
silence into false reassurance. Absence is evidence of search, not
evidence of safety.

Doctrine:
  - every query result carries its SEARCH SET: the stores, filters,
    and as-of stamp actually consulted
  - an absence claim enumerates what was checked; anything not in the
    search set is NOT-CHECKED, stated, never implied clear
  - a claim whose search set is empty REFUSES — "nothing found" with
    nothing searched is a lie with a confident tone
  - partial coverage is first-class: checked stores say absent/clear,
    unchecked stores say not-checked, and the envelope never merges
    the two into one comforting word
"""

from __future__ import annotations

import hashlib
import json

VERSION = "absentia@1.0.0"


class AbsentiaError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class QueryEnvelope:
    """Wrap a query result with the search set that produced it."""

    def __init__(self, subject, *, as_of):
        if not subject:
            raise AbsentiaError("an envelope needs a subject")
        if not as_of:
            raise AbsentiaError("an absence claim without an as-of "
                                "stamp is unfalsifiable")
        self.subject = subject
        self.as_of = as_of
        self._checked = []
        self._findings = {}
        self._not_checked = []

    def record_search(self, store, *, filters=None, findings=()):
        if not store:
            raise AbsentiaError("a search needs a named store")
        entry = {"store": store, "filters": dict(filters or {}),
                 "n": len(findings)}
        self._checked.append(entry)
        self._findings[store] = list(findings)
        return entry

    def declare_not_checked(self, store, *, reason):
        """Unchecked is stated, never silent."""
        if not reason:
            raise AbsentiaError("a not-checked declaration needs a "
                                "reason — silent gaps are the defect "
                                "this module exists to prevent")
        self._not_checked.append({"store": store, "reason": reason})

    def absence_claim(self, kind):
        """'No <kind> found' — valid only over the checked set."""
        if not self._checked:
            raise AbsentiaError(
                f"cannot claim absence of {kind}: nothing was searched")
        absent_in = [c["store"] for c in self._checked
                     if not self._findings[c["store"]]]
        found_in = [c["store"] for c in self._checked
                    if self._findings[c["store"]]]
        claim = {
            "kind": kind, "subject": self.subject, "as_of": self.as_of,
            "searched": [c["store"] for c in self._checked],
            "absent_in": absent_in, "found_in": found_in,
            "not_checked": list(self._not_checked),
        }
        if not absent_in and not self._not_checked:
            claim["verdict"] = f"{kind} present"
        elif found_in:
            claim["verdict"] = (f"{kind} found in {found_in}; absent "
                                f"from {absent_in}")
        elif self._not_checked:
            claim["verdict"] = (f"{kind} absent from searched stores "
                                f"{absent_in}; NOT CHECKED: "
                                f"{[n['store'] for n in self._not_checked]}")
        else:
            claim["verdict"] = f"{kind} absent from all searched stores"
        claim["fingerprint"] = hashlib.sha256(_canon(
            {k: v for k, v in claim.items() if k != "fingerprint"}
        )).hexdigest()
        return claim
