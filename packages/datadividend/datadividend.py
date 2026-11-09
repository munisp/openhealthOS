
"""datadividend.py — the patient data-use ledger.

Every EMR promises 'your data is safe'; none answer 'what did my data
DO?'. This module keeps a per-patient ledger of every secondary use
(research cohort, registry extract, quality metric), each entry
consent-bound and k-anonymized, and generates the share-back statement
a patient can actually read: your record contributed to 3 studies,
one of which changed the malaria guideline.

Doctrine:
- USE WITHOUT CONSENT SCOPE NEVER ENTERS THE LEDGER: record_use()
  requires a consent_ref whose scope covers the purpose; the ledger
  refuses otherwise.
- K-FLOOR ON COHORTS: a use describing fewer than K patients is
  refused — small cohorts re-identify.
- SHARE-BACK IS PLAIN LANGUAGE: patient_statement() renders counts
  and purposes, never record-level detail.
"""

VERSION = "datadividend@1.0.0"
K_FLOOR = 5

import hashlib
import json


class DividendError(Exception):
    pass


class ConsentScopes:
    """patient_ref -> set of purpose scopes consented, each with a
    consent_ref (the signed artifact upstream)."""

    def __init__(self):
        self._consents = {}

    def grant(self, patient_ref, *, purpose, consent_ref):
        if not consent_ref:
            raise DividendError("consent needs its signed artifact ref")
        self._consents.setdefault(patient_ref, {})[purpose] = consent_ref

    def covers(self, patient_ref, purpose):
        return self._consents.get(patient_ref, {}).get(purpose)


class DataUseLedger:
    def __init__(self, consents):
        self.consents = consents
        self._entries = []

    def record_use(self, patient_ref, *, purpose, study_ref, cohort_size,
                   day, outcome=""):
        consent = self.consents.covers(patient_ref, purpose)
        if consent is None:
            raise DividendError(
                f"no consent scope '{purpose}' for {patient_ref} — "
                f"the use never enters the ledger")
        if cohort_size < K_FLOOR:
            raise DividendError(
                f"cohort of {cohort_size} re-identifies — k-floor is "
                f"{K_FLOOR}")
        entry = {"patient_ref": patient_ref, "purpose": purpose,
                 "study_ref": study_ref, "cohort_size": cohort_size,
                 "day": day, "outcome": outcome, "consent_ref": consent}
        entry["checksum"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True).encode()).hexdigest()
        self._entries.append(entry)
        return entry["checksum"]

    def patient_statement(self, patient_ref):
        mine = [e for e in self._entries if e["patient_ref"] == patient_ref]
        if not mine:
            return {"patient_ref": patient_ref, "uses": 0,
                    "statement": "Your record has not been used in any "
                                 "research or registry extract."}
        purposes = sorted({e["purpose"] for e in mine})
        outcomes = [e["outcome"] for e in mine if e["outcome"]]
        stmt = (f"Your record contributed to {len(mine)} approved use(s) "
                f"across {len(purposes)} purpose(s): "
                f"{', '.join(purposes)}.")
        if outcomes:
            stmt += " Reported outcomes: " + "; ".join(outcomes) + "."
        return {"patient_ref": patient_ref, "uses": len(mine),
                "purposes": purposes, "statement": stmt}

    def verify(self):
        """Re-checksum every entry; tampering anywhere fails loudly."""
        for e in self._entries:
            body = {k: v for k, v in e.items() if k != "checksum"}
            if hashlib.sha256(json.dumps(
                    body, sort_keys=True).encode()).hexdigest() != e["checksum"]:
                raise DividendError(
                    f"ledger tampered at {e['patient_ref']}/{e['study_ref']}")
        return True
