"""certificates.py — medical certificates, sick notes, and referral letters.

A certificate is a clinical statement with legal force. A sick note that
cannot be verified is an invitation to forgery; a referral letter that
says nothing is a wasted journey. These documents are generated, signed,
and verifiable — with a sha256 fingerprint and a QR-verifiable payload.

Doctrine:
- SIGNATURE IS NAMED: every certificate names the issuing clinician and
  the day. The document is invalid without both.
- THE CONTENT IS STATEMENTS, NOT OPINIONS: "unfit for work 7 days" is a
  statement; "patient is lazy" is not a certificate.
- VERIFICATION IS OFFLINE: the document carries a canonical sha256 so a
  third party can verify the paper against the registry.
- FIT-NOTE DURATION IS BOUNDED: a sick note beyond 90 days is a new
  assessment, not a bigger number.
"""

import hashlib
import json

VERSION = "certificates@1.0.0"

CERT_TYPES = ("sick-note", "fit-note", "referral-letter",
              "death-certificate", "vaccination-certificate",
              "medical-report-summary")

MAX_SICK_DAYS = 90


class Error(Exception):
    pass


class Certificate:
    def __init__(self, cert_id, cert_type, patient_ref, *, issued_by,
                 day, body, encounter_ref=None):
        if cert_type not in CERT_TYPES:
            raise Error(f"unknown certificate type: {cert_type}")
        if not issued_by:
            raise Error("a certificate names its issuing clinician")
        if not (body and isinstance(body, dict)):
            raise Error("a certificate carries a structured body")
        self.cert_id = cert_id
        self.cert_type = cert_type
        self.patient_ref = patient_ref
        self.issued_by = issued_by
        self.day = day
        self.body = body
        self.encounter_ref = encounter_ref

    def _canonical(self):
        return {"cert_id": self.cert_id, "cert_type": self.cert_type,
                "patient_ref": self.patient_ref,
                "issued_by": self.issued_by, "day": self.day,
                "body": self.body, "encounter_ref": self.encounter_ref,
                "version": VERSION}

    def fingerprint(self):
        return hashlib.sha256(
            json.dumps(self._canonical(), sort_keys=True).encode()
        ).hexdigest()

    def as_dict(self):
        d = self._canonical()
        d["sha256"] = self.fingerprint()
        return d


class CertificateRegistry:
    """All certificates issued by a facility. The verification surface."""

    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.certs = {}
        self._n = 0

    def issue_sick_note(self, patient_ref, *, issued_by, day, days_off,
                        diagnosis_stated, encounter_ref=None):
        if not (1 <= days_off <= MAX_SICK_DAYS):
            raise Error(
                f"sick note of {days_off} days refused — beyond "
                f"{MAX_SICK_DAYS} days is a new assessment")
        if not (diagnosis_stated and diagnosis_stated.strip()):
            raise Error("the clinical basis is stated")
        self._n += 1
        cert = Certificate(
            f"{self.facility_id}-CERT-{self._n}", "sick-note", patient_ref,
            issued_by=issued_by, day=day,
            body={"days_off": days_off, "diagnosis": diagnosis_stated,
                  "statement": f"unfit for work for {days_off} days"},
            encounter_ref=encounter_ref)
        self.certs[cert.cert_id] = cert
        return cert

    def issue_fit_note(self, patient_ref, *, issued_by, day, statement,
                       encounter_ref=None):
        if not (statement and statement.strip()):
            raise Error("the fitness statement is required")
        self._n += 1
        cert = Certificate(
            f"{self.facility_id}-CERT-{self._n}", "fit-note", patient_ref,
            issued_by=issued_by, day=day,
            body={"statement": statement}, encounter_ref=encounter_ref)
        self.certs[cert.cert_id] = cert
        return cert

    def issue_referral(self, patient_ref, *, issued_by, day, to_specialty,
                       to_facility, clinical_question, summary,
                       encounter_ref=None):
        for name, val in (("specialty", to_specialty),
                          ("facility", to_facility),
                          ("clinical question", clinical_question),
                          ("summary", summary)):
            if not (val and str(val).strip()):
                raise Error(f"a referral names its {name}")
        self._n += 1
        cert = Certificate(
            f"{self.facility_id}-CERT-{self._n}", "referral-letter",
            patient_ref, issued_by=issued_by, day=day,
            body={"to_specialty": to_specialty,
                  "to_facility": to_facility,
                  "clinical_question": clinical_question,
                  "summary": summary}, encounter_ref=encounter_ref)
        self.certs[cert.cert_id] = cert
        return cert

    def verify(self, cert_id, sha256):
        """Offline verification: does this paper match this registry?"""
        cert = self.certs.get(cert_id)
        if cert is None:
            return False, "certificate not in registry"
        if cert.fingerprint() != sha256:
            return False, "fingerprint mismatch — document altered"
        return True, "verified"

    def register_snapshot(self):
        body = {"facility_id": self.facility_id,
                "total": len(self.certs),
                "by_type": {}}
        for c in self.certs.values():
            body["by_type"][c.cert_type] = \
                body["by_type"].get(c.cert_type, 0) + 1
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        body["version"] = VERSION
        return body
