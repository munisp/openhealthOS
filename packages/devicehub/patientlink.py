"""patientlink.py — device results meet the right chart.

Doctrine:
  - A RESULT WITHOUT A KNOWN PATIENT IS QUARANTINED, NOT GUESSED:
    the wrong chart is worse than no chart.
  - The crosswalk is versioned, append-only, and merge-aware
    (ADT^A40): MRNs retire, patients merge, the map must remember
    both ends.
  - ADT feeds keep the map warm; an analyzer's PID is a claim to be
    resolved, never an identity.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devicehub import DeviceHubError, parse_oru  # noqa: E402,F401

PATIENTLINK_VERSION = "patient-link@1.0.0"


def parse_adt(message):
    """Parse ADT^A01/A08 (register/update) and ADT^A40 (merge).

    Returns {event, mrn, ...}. A40 carries the retiring MRN in MRG-1.
    Anything else refuses — an unhandled ADT event silently half-applied
    is how charts split."""
    segments = [s for s in message.replace("\n", "\r").split("\r")
                if s.strip()]
    if not segments or not segments[0].startswith("MSH"):
        raise DeviceHubError("not HL7 — refuse, don't guess")
    msh = segments[0].split("|")
    if len(msh) < 9 or not msh[8].startswith("ADT"):
        raise DeviceHubError(f"not an ADT message: "
                             f"{msh[8] if len(msh) > 8 else '?'}")
    event = msh[8].split("^")[1] if "^" in msh[8] else ""
    pid = next((s.split("|") for s in segments if s.startswith("PID")),
               None)
    if pid is None or len(pid) < 4:
        raise DeviceHubError("ADT without a PID segment — a patient "
                             "event about nobody")
    mrn = pid[3].split("^")[0]
    out = {"event": event, "mrn": mrn,
           "name": pid[5].replace("^", " ") if len(pid) > 5 else ""}
    if event == "A40":
        mrg = next((s.split("|") for s in segments
                    if s.startswith("MRG")), None)
        if mrg is None or len(mrg) < 2 or not mrg[1]:
            raise DeviceHubError("A40 merge without MRG-1 — a merge "
                                 "that cannot name the retiring MRN "
                                 "is a split, not a merge")
        out["retired_mrn"] = mrg[1].split("^")[0]
    elif event not in ("A01", "A04", "A08"):
        raise DeviceHubError(f"unhandled ADT event {event!r} — the "
                             "handled set is A01/A04/A08/A40; extend "
                             "the codec, never half-apply")
    return out


class IdentityMap:
    """MRN → platform patient id. Append-only event log; resolution is
    a lookup, merges follow the chain to the surviving MRN."""

    def __init__(self):
        self.bindings = {}   # mrn -> platform_id
        self.merges = {}     # retired_mrn -> surviving_mrn
        self.events = []     # append-only
        self.quarantine = []  # results about unknown patients

    def register(self, mrn, platform_id, *, by, source="adt"):
        if not mrn or not platform_id:
            raise DeviceHubError("a binding needs both ends — an MRN "
                                 "to nowhere maps nothing")
        self.bindings[mrn] = platform_id
        self.events.append({"event": "register", "mrn": mrn,
                            "platform_id": platform_id, "by": by,
                            "source": source})

    def merge(self, surviving_mrn, retired_mrn, *, by):
        if retired_mrn not in self.bindings:
            raise DeviceHubError(f"merge source {retired_mrn} is not "
                                 "bound — merging an unknown patient "
                                 "is a chart split, not a merge")
        self.merges[retired_mrn] = surviving_mrn
        self.bindings[surviving_mrn] = self.bindings[retired_mrn]
        self.events.append({"event": "merge",
                            "surviving": surviving_mrn,
                            "retired": retired_mrn, "by": by})

    def resolve(self, mrn, *, _depth=0):
        """Follow merges to the surviving binding. None means unknown
        — and unknown is stated, never defaulted."""
        if _depth > 10:
            raise DeviceHubError("merge chain loops — the map is "
                                 "corrupt; stop, don't recurse forever")
        if mrn in self.merges:
            return self.resolve(self.merges[mrn], _depth=_depth + 1)
        return self.bindings.get(mrn)

    def apply_adt(self, message, *, by):
        adt = parse_adt(message)
        if adt["event"] == "A40":
            self.merge(adt["mrn"], adt["retired_mrn"], by=by)
        else:
            # A01/A04/A08 bind the MRN to itself pending a platform id
            # assignment — the registration counter is the source
            self.bindings.setdefault(adt["mrn"], adt["mrn"])
            self.events.append({"event": adt["event"],
                                "mrn": adt["mrn"], "by": by})
        return adt

    def route_result(self, parsed):
        """Resolve a parsed ORU's patient. Unknown → quarantined with
        the full payload, loudly countable. Known → stamped with the
        platform id."""
        mrn = parsed.get("patient_id")
        platform_id = self.resolve(mrn) if mrn else None
        if platform_id is None:
            self.quarantine.append({
                "message_id": parsed.get("message_id"),
                "claimed_mrn": mrn,
                "observations": len(parsed.get("observations", [])),
                "reason": "patient unknown to the crosswalk — a result "
                          "without a known chart waits; it never "
                          "guesses"})
            return {"routed": False, "quarantined": True,
                    "message_id": parsed.get("message_id")}
        parsed["platform_patient_id"] = platform_id
        return {"routed": True, "platform_patient_id": platform_id,
                "message_id": parsed.get("message_id")}

    def quarantine_report(self):
        return {"count": len(self.quarantine),
                "entries": [dict(q) for q in self.quarantine],
                "note": "every entry is a result waiting for identity "
                        "— the count is the worklist, not a statistic",
                "version": PATIENTLINK_VERSION}
