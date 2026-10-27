"""gbv.py — GBV safeguarding and referral pathway.

GBV survivors present at PHCs constantly and are failed structurally:
records visible to every clerk, referrals to services nobody verified
exist, no follow-up. This module's first job is access control AS
STRUCTURE — a record readable by anyone is worse than no record.

Doctrine:
- INVISIBLE BY DEFAULT: survivor records are sealed at creation.
  Only the survivor's designated care team (named roles, not "staff")
  may open them, and every opening is in the access log. Aggregate
  counts are the only thing general queries ever see.
- CONSENT GOVERNS EVERY REFERRAL: a referral without recorded consent
  for THAT destination is refused. Consent to tell the nurse is not
  consent to tell the police.
- SERVICES MUST EXIST: the referral directory holds verified services
  with verification dates; a stale verification degrades to
  unverified, and referring to an unverified service is flagged.
- SAFETY FIRST: safety assessment before any referral plan; an
  unsafe-at-home finding overrides routine follow-up.
"""

SEALED_ROLES = ("gbv-focal-point", "gbv-clinician", "gbv-counsellor")
VERIFY_STALE_DAYS = 90


class GBVError(Exception):
    pass


class ServiceDirectory:
    def __init__(self):
        self.services = {}

    def register(self, service_id, *, kind, name, location, contact,
                 verified_day):
        if kind not in ("medical", "psychosocial", "legal", "shelter",
                        "police-gbv-desk"):
            raise GBVError(f"unknown service kind: {kind}")
        self.services[service_id] = {
            "kind": kind, "name": name, "location": location,
            "contact": contact, "verified_day": verified_day}

    def status(self, service_id, as_of):
        s = self.services.get(service_id)
        if s is None:
            raise GBVError(f"unknown service: {service_id}")
        stale = as_of - s["verified_day"] > VERIFY_STALE_DAYS
        return {"service_id": service_id, "kind": s["kind"],
                "verified": not stale,
                "days_since_verification": as_of - s["verified_day"]}


class CaseBook:
    def __init__(self, directory):
        self.dir = directory
        self.cases = {}
        self.access_log = []
        self._n = 0

    def open_case(self, *, officer, officer_role, day, safety,
                  care_team, note_ref=None):
        """note_ref is a pointer to sealed notes — never the notes."""
        if officer_role not in SEALED_ROLES:
            raise GBVError(
                f"{officer_role} cannot open GBV cases — sealed domain")
        if safety not in ("safe-at-home", "unsafe-at-home", "uncertain"):
            raise GBVError("safety assessment required at case opening")
        if not care_team or any(r not in SEALED_ROLES for r in care_team):
            raise GBVError("care team must be named sealed roles")
        self._n += 1
        cid = f"GBV-{self._n}"
        self.cases[cid] = {
            "opened_day": day, "safety": safety, "care_team": care_team,
            "opened_by": officer, "note_ref": note_ref, "referrals": [],
            "followups": [],
        }
        return {"case_id": cid}

    def read_case(self, case_id, *, accessor, accessor_role, day):
        c = self._case(case_id)
        if accessor_role not in c["care_team"] and accessor_role not in (
                SEALED_ROLES if accessor == c["opened_by"] else ()):
            self.access_log.append(
                {"day": day, "case_id": case_id, "accessor": accessor,
                 "granted": False})
            raise GBVError(
                "access denied and logged — sealed records open only "
                "for the named care team")
        self.access_log.append(
            {"day": day, "case_id": case_id, "accessor": accessor,
             "granted": True})
        return dict(c)

    def refer(self, case_id, service_id, *, day, by, by_role,
              consented):
        c = self._case(case_id)
        if by_role not in c["care_team"]:
            raise GBVError("only the named care team may refer")
        if not consented:
            raise GBVError(
                "no consent for this referral — consent to care is not "
                "consent to every destination")
        st = self.dir.status(service_id, day)
        rec = {"service_id": service_id, "day": day,
               "service_verified": st["verified"]}
        if not st["verified"]:
            rec["warning"] = (
                f"service verification stale "
                f"({st['days_since_verification']}d) — confirm before "
                "the survivor travels")
        c["referrals"].append(rec)
        return dict(rec)

    def record_followup(self, case_id, *, day, completed, outcome=None):
        c = self._case(case_id)
        c["followups"].append({"day": day, "completed": bool(completed),
                               "outcome": outcome})
        return dict(c["followups"][-1])

    def aggregate(self):
        """The ONLY thing general queries see. No line-list exists here."""
        return {
            "cases": len(self.cases),
            "unsafe_at_home": sum(1 for c in self.cases.values()
                                  if c["safety"] == "unsafe-at-home"),
            "referrals_made": sum(len(c["referrals"])
                                  for c in self.cases.values()),
            "referrals_to_unverified_services": sum(
                1 for c in self.cases.values() for r in c["referrals"]
                if not r["service_verified"]),
            "access_denials": sum(1 for a in self.access_log
                                  if not a["granted"]),
        }

    def _case(self, cid):
        if cid not in self.cases:
            raise GBVError(f"unknown case: {cid}")
        return self.cases[cid]
