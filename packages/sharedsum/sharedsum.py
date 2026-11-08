"""sharedsum.py — national shared patient summary (RSE/PDS pattern).

Portugal's RSE exists so that a hospital in Braga can read what a
hospital in Faro did. The summary is not the record — it is the
nationally-agreed minimum: problems, medications, allergies, recent
encounters, contributed by facilities and readable under consent.

Doctrine:
- FACILITIES CONTRIBUTE, THE NATION AGGREGATES: every summary section
  carries its contributor facility and contribution day. A summary
  without provenance is gossip.
- CONSENT IS THE DEFAULT GATE: access requires a consent context —
  care relationship, emergency break-glass with stated reason, or
  citizen grant. No context, no summary.
- BREAK-GLASS IS LOUD: emergency access names the accessor, the reason,
  and lands on the citizen's audit trail. It is never quiet.
- OPT-OUT IS HONOURED: a citizen who restricts their summary gets it
  restricted — the restriction itself is all a reader may learn.
- THE SUMMARY IS CHECKSUMMED: what you read is what was contributed;
  the fingerprint travels with the payload.
"""

import hashlib
import json

VERSION = "sharedsum@1.0.0"

SUMMARY_SECTIONS = ("problems", "medications", "allergies",
                    "encounters", "immunizations", "observations")

ACCESS_CONTEXTS = ("care-relationship", "citizen-grant", "break-glass")


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class SummaryContribution:
    """One facility's contribution to a citizen's national summary."""

    def __init__(self, patient_ref, facility, section, day, entries,
                 *, contributed_by):
        if section not in SUMMARY_SECTIONS:
            raise Error(f"unknown summary section {section!r}; sections "
                        f"are {SUMMARY_SECTIONS}")
        if not contributed_by:
            raise Error("a contribution names its contributor")
        if not entries:
            raise Error("an empty contribution is noise, not data")
        self.patient_ref = patient_ref
        self.facility = facility
        self.section = section
        self.day = day
        self.entries = list(entries)
        self.contributed_by = contributed_by

    def as_dict(self):
        return {"patient_ref": self.patient_ref,
                "facility": self.facility, "section": self.section,
                "day": self.day, "entries": self.entries,
                "contributed_by": self.contributed_by,
                "version": VERSION}

    def fingerprint(self):
        return _sha(self.as_dict())


class SharedSummaryService:
    """The national service. It does not diagnose, merge, or reconcile —
    it stores attributed contributions and gates the read."""

    def __init__(self):
        self.contributions = {}   # patient_ref -> [SummaryContribution]
        self.restricted = {}      # patient_ref -> {day, by, scope}
        self.access_log = []      # append-only

    # --- citizen rights ----------------------------------------------
    def restrict(self, patient_ref, *, by, day, scope="all"):
        """The citizen restricts their summary. scope 'all' hides every
        section; a section name hides only that section."""
        if scope != "all" and scope not in SUMMARY_SECTIONS:
            raise Error(f"unknown restriction scope {scope!r}")
        if by != patient_ref:
            raise Error("only the citizen restricts their own summary")
        self.restricted[patient_ref] = {"day": day, "scope": scope}
        self.access_log.append({"event": "restricted", "day": day,
                                "patient_ref": patient_ref,
                                "scope": scope})

    def lift_restriction(self, patient_ref, *, by, day):
        if by != patient_ref:
            raise Error("only the citizen lifts their restriction")
        if patient_ref not in self.restricted:
            raise Error("no restriction to lift")
        del self.restricted[patient_ref]
        self.access_log.append({"event": "restriction-lifted",
                                "day": day, "patient_ref": patient_ref})

    # --- contribution --------------------------------------------------
    def contribute(self, contribution):
        self.contributions.setdefault(
            contribution.patient_ref, []).append(contribution)
        return contribution.fingerprint()

    # --- access ---------------------------------------------------------
    def read(self, patient_ref, *, accessor, facility, day, context,
             reason=""):
        """Read the national summary. Context is mandatory; break-glass
        states a reason; restriction hides before anything is read."""
        if context not in ACCESS_CONTEXTS:
            raise Error(f"unknown access context {context!r}; contexts "
                        f"are {ACCESS_CONTEXTS}")
        if not accessor:
            raise Error("every read names its reader")
        if context == "break-glass" and not reason:
            raise Error("break-glass access states its reason — an "
                        "emergency is a claim, not a password")
        restriction = self.restricted.get(patient_ref)
        if restriction is not None and context != "break-glass":
            self.access_log.append({
                "event": "read-refused-restricted", "day": day,
                "patient_ref": patient_ref, "accessor": accessor,
                "context": context})
            raise Error("this citizen has restricted their summary; "
                        "the restriction is all you may learn")
        contribs = self.contributions.get(patient_ref, [])
        sections = {}
        for c in contribs:
            if (restriction is not None
                    and restriction["scope"] == c.section):
                continue  # break-glass does not unwrap a named section
            sections.setdefault(c.section, []).append(c.as_dict())
        event = {"event": "read", "day": day, "patient_ref": patient_ref,
                 "accessor": accessor, "facility": facility,
                 "context": context}
        if context == "break-glass":
            event["break_glass_reason"] = reason
        self.access_log.append(event)
        payload = {"patient_ref": patient_ref,
                   "restricted": restriction is not None,
                   "sections": sections, "read_by": accessor,
                   "context": context, "version": VERSION}
        payload["sha256"] = _sha({k: v for k, v in payload.items()
                                  if k != "sha256"})
        return payload

    # --- accountability ---------------------------------------------------
    def citizen_audit(self, patient_ref, *, by):
        """The citizen sees everyone who touched their summary."""
        if by != patient_ref:
            raise Error("the audit trail belongs to the citizen")
        return [e for e in self.access_log
                if e.get("patient_ref") == patient_ref]

    def break_glass_report(self, day):
        """Oversight: every break-glass, stated. A quiet override is a
        breach; a listed one is governance."""
        return [e for e in self.access_log
                if e.get("context") == "break-glass"
                and e["event"] == "read"]

    def coverage(self):
        return {"patients": len(self.contributions),
                "contributions": sum(len(v)
                                     for v in self.contributions.values()),
                "restricted": len(self.restricted),
                "reads": sum(1 for e in self.access_log
                             if e["event"] == "read"),
                "version": VERSION}
