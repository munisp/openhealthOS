"""xrelease.py — consent-gated cross-border release of patient data.

The IPS exists to travel with the patient; this module decides when it
may leave the jurisdiction. Nigeria's NDPR/NDPA and every receiving
country's law agree on one thing: a clinical record crosses a border
on consent or on a documented emergency — never on convenience.

Doctrine:
- CONSENT IS PER-RELEASE, PER-DESTINATION: consent to treat is not
  consent to export. Each release names patient, destination country,
  receiving facility, data covered (the IPS bundle hash), and purpose.
- THE HASH IS THE CONTRACT: the release binds to the IPS bundle's
  fingerprint; transmitting a different bundle than the one consented
  to is detected, not just wrong.
- EMERGENCY EXCEPTION IS DOUBLE-LOCKED: an incapacitated patient can
  be released on a senior clinician PLUS an administrator attesting
  incapacity and necessity — two signatures, both named, both on the
  record, flagged for retrospective review within 72h.
- RELEASE LOG IS APPEND-ONLY AND AUDITABLE: every release (and every
  transmission attempt) is recorded; the patient can later be shown
  exactly what went where. Denials are logged too.
- REVOCATION STOPS FUTURE TRANSMISSIONS: already-transmitted data
  can't be un-sent — the record says so honestly.
"""

EMERGENCY_REVIEW_WINDOW_DAYS = 3


class XReleaseError(Exception):
    pass


class ReleaseBook:
    def __init__(self):
        self.releases = {}
        self.log = []   # append-only
        self._n = 0

    def request_release(self, *, patient_id, destination_country,
                        receiving_facility, ips_bundle_hash, purpose):
        if not all([patient_id, destination_country, receiving_facility,
                    ips_bundle_hash, purpose]):
            raise XReleaseError(
                "release requires patient, destination, facility, "
                "bundle hash, and purpose — all five")
        self._n += 1
        rid = f"REL-{self._n}"
        self.releases[rid] = {
            "id": rid, "patient_id": patient_id,
            "destination_country": destination_country,
            "receiving_facility": receiving_facility,
            "ips_bundle_hash": ips_bundle_hash, "purpose": purpose,
            "state": "pending-consent", "transmissions": [],
        }
        return dict(self.releases[rid])

    def grant_consent(self, rid, *, day, consented_by, method):
        """method: signed | verbal-witnessed | next-of-kin (with name)."""
        r = self._rel(rid)
        self._require(r, "pending-consent")
        if method not in ("signed", "verbal-witnessed", "next-of-kin"):
            raise XReleaseError(f"unknown consent method: {method}")
        r["consent"] = {"day": day, "by": consented_by, "method": method}
        r["state"] = "consented"
        self._log(rid, day, "consent-granted", consented_by)
        return dict(r)

    def emergency_override(self, rid, *, day, senior_clinician,
                           administrator, incapacity_reason):
        """Two named officers, documented incapacity, auto-flagged for
        retrospective review."""
        r = self._rel(rid)
        self._require(r, "pending-consent")
        if senior_clinician == administrator:
            raise XReleaseError(
                "emergency override needs TWO DIFFERENT officers")
        if not incapacity_reason:
            raise XReleaseError("incapacity must be documented")
        r["consent"] = {"day": day, "method": "emergency-override",
                        "senior_clinician": senior_clinician,
                        "administrator": administrator,
                        "incapacity": incapacity_reason,
                        "retrospective_review_due":
                            day + EMERGENCY_REVIEW_WINDOW_DAYS}
        r["state"] = "consented"
        self._log(rid, day, "emergency-override",
                  f"{senior_clinician}+{administrator}")
        return dict(r)

    def transmit(self, rid, *, day, bundle_hash, channel, transmitted_by):
        """The hash check: transmitting anything other than the
        consented bundle refuses and logs."""
        r = self._rel(rid)
        self._require(r, "consented")
        if bundle_hash != r["ips_bundle_hash"]:
            self._log(rid, day, "transmission-refused-hash-mismatch",
                      transmitted_by)
            raise XReleaseError(
                "bundle hash does not match the consented release — "
                "refused and logged")
        rec = {"day": day, "channel": channel, "by": transmitted_by,
               "hash": bundle_hash}
        r["transmissions"].append(rec)
        self._log(rid, day, "transmitted", transmitted_by)
        return dict(rec)

    def revoke(self, rid, *, day, by):
        """Stops future transmissions; honest about the past."""
        r = self._rel(rid)
        if r["state"] != "consented":
            raise XReleaseError(f"cannot revoke from state {r['state']}")
        r["state"] = "revoked"
        self._log(rid, day, "consent-revoked", by)
        return {"revoked": True, "already_transmitted":
                len(r["transmissions"]),
                "note": "revocation stops future transmissions; sent "
                        "data cannot be un-sent"}

    def overrides_awaiting_review(self, as_of):
        out = []
        for r in self.releases.values():
            c = r.get("consent") or {}
            due = c.get("retrospective_review_due")
            if due is not None and not c.get("reviewed") and as_of > due:
                out.append({"release_id": r["id"],
                            "patient_id": r["patient_id"],
                            "days_overdue": as_of - due})
        return out

    def record_review(self, rid, *, day, reviewer, finding):
        r = self._rel(rid)
        c = r.get("consent") or {}
        if c.get("method") != "emergency-override":
            raise XReleaseError("only emergency overrides are reviewed")
        c["reviewed"] = {"day": day, "reviewer": reviewer,
                         "finding": finding}
        self._log(rid, day, "override-reviewed", reviewer)
        return dict(r)

    def audit_trail(self, rid):
        self._rel(rid)
        return [e for e in self.log if e["release_id"] == rid]

    def _rel(self, rid):
        if rid not in self.releases:
            raise XReleaseError(f"unknown release: {rid}")
        return self.releases[rid]

    def _log(self, rid, day, event, actor):
        self.log.append({"release_id": rid, "day": day, "event": event,
                         "actor": actor})

    @staticmethod
    def _require(r, state):
        if r["state"] != state:
            raise XReleaseError(f"{r['id']} is {r['state']}, expected {state}")
