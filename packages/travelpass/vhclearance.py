"""vhclearance.py — batch manifest pre-clearance: the airline sends the
manifest before departure; every passenger comes back with a named
status before anyone reaches the border.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.3):
  - FR-VER-04: batch manifest pre-clearance — per-passenger
    cleared / cleared-exempt / review / not-cleared, counts with
    denominators, the whole manifest checksummed so the gate and the
    scheme are provably looking at the same file.

Doctrines:
- Pre-clearance is advice with a checksum, not a hope: the manifest
  result carries its sha256 — the checksum is the contract.
- A passenger with no record is not a passenger with no cover; they
  are a passenger the scheme has not met — status says so.
- The manifest never learns a name back: statuses ride on the row
  indices the airline sent.
"""

import hashlib
import json

VHCLEARANCE_VERSION = "vhc-clearance@1.0.0"

PRECLEAR_STATUSES = ("cleared", "cleared-exempt", "review",
                     "not-cleared")


class ClearanceError(Exception):
    pass


def _refuse(msg):
    raise ClearanceError(msg)


def preclear_manifest(vhc, *, flight_ref, rows, travel_day,
                      submitted_day):
    """FR-VER-04: rows are [{passport_ref, dob}] as the airline sent
    them. Each row returns a status with its reason; the result is
    checksummed over the row indices, never the names."""
    if not flight_ref:
        _refuse("a manifest names its flight")
    if not rows:
        _refuse("an empty manifest pre-clears nothing")
    results = []
    counts = {s: 0 for s in PRECLEAR_STATUSES}
    for i, row in enumerate(rows):
        if not row.get("passport_ref"):
            status, reason = "review", "row missing passport — to the desk"
        else:
            found = vhc.desk_lookup(passport_ref=row["passport_ref"],
                                    dob=row.get("dob"))
            status, reason = _classify(vhc, found, travel_day)
        results.append({"row": i, "status": status, "reason": reason})
        counts[status] += 1
    body = {"flight_ref": flight_ref, "travel_day": travel_day,
            "submitted_day": submitted_day, "rows": results,
            "counts": dict(counts, total=len(rows))}
    body["manifest_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode()).hexdigest()
    return body


def _classify(vhc, found, travel_day):
    if found["count"] == 0:
        return ("not-cleared",
                "no record on file — a traveller the scheme has not "
                "met; purchase or exemption required before boarding")
    best = None
    for m in found["matches"]:
        if m["kind"] == "exemption":
            if m["expires_day"] >= travel_day:
                return ("cleared-exempt",
                        "exempt: %s (evidence on file, valid to day %d)"
                        % (m["category"], m["expires_day"]))
            best = ("review", "exemption expired before travel day")
            continue
        win = m["window"]
        if win["from"] <= travel_day <= win["to"]:
            if win["to"] - travel_day <= \
                    vhc.profile["expiring_soon_days"]:
                cand = ("review",
                        "cover expires within 72h of travel day")
                best = best or cand
            else:
                return ("cleared", "cover valid on travel day")
        elif travel_day < win["from"]:
            return ("cleared", "cover begins before travel day")
        else:
            best = ("review", "cover lapsed before travel day — "
                              "extension possible")
    return best or ("review", "record requires desk review")
