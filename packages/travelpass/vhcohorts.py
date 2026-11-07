"""Campus cohorts — semester cover sold where the students are.

A cohort is a named group (a university's international students, a
conference delegation) with a per-cohort block price and member
references (matriculation numbers, delegate ids). The cohort's home
campus clinic accredits as a provider role, so care lands where the
cohort lives.

Doctrine: the cohort price is a versioned entry ratified four-eyes;
a member not on the manifest is not covered — a name not on the list
is a claim waiting to be refused.
"""

import hashlib

COHORT_VERSION = "vhcohorts@1.0.0"


class CohortError(Exception):
    pass


def _refuse(msg):
    raise CohortError(msg)


class CohortRegistry:
    def __init__(self):
        self.cohorts = {}
        self.audit = []

    def create(self, cohort_id, *, institution, country, term_days,
               price_per_member_minor, currency, ratified_by,
               approved_by, day, campus_provider_ref=None):
        if not cohort_id or not institution:
            _refuse("a cohort names itself and its institution")
        if cohort_id in self.cohorts:
            _refuse("duplicate cohort id")
        if not ratified_by or not approved_by or ratified_by == approved_by:
            _refuse("the hand that proposes never approves")
        if price_per_member_minor <= 0 or term_days < 1:
            _refuse("a cohort price and term are positive, stated numbers")
        self.cohorts[cohort_id] = {
            "cohort_id": cohort_id, "institution": institution,
            "country": country, "term_days": term_days,
            "price_per_member_minor": price_per_member_minor,
            "currency": currency,
            "campus_provider_ref": campus_provider_ref,
            "ratified_by": ratified_by, "approved_by": approved_by,
            "members": {}, "state": "open", "created_day": day,
        }
        self.audit.append({"event": "cohort_created",
                           "cohort": cohort_id, "day": day})
        return self._public(cohort_id)

    def _cohort(self, cohort_id):
        c = self.cohorts.get(cohort_id)
        if c is None:
            _refuse("unknown cohort")
        if c["state"] != "open":
            _refuse("cohort closed — the roster is frozen at census")
        return c

    def add_member(self, cohort_id, *, member_ref, full_name, day):
        c = self._cohort(cohort_id)
        if not member_ref or not full_name:
            _refuse("a member is a reference and a name")
        if member_ref in c["members"]:
            _refuse("duplicate member — a matric number appears once")
        c["members"][member_ref] = {"member_ref": member_ref,
                                    "full_name": full_name,
                                    "added_day": day,
                                    "cover_ref": None}
        self.audit.append({"event": "cohort_member_added",
                           "cohort": cohort_id, "member": member_ref,
                           "day": day})
        return dict(c["members"][member_ref])

    def close_census(self, cohort_id, *, day, by):
        c = self._cohort(cohort_id)
        if not by:
            _refuse("a census closes under a name")
        if not c["members"]:
            _refuse("an empty cohort covers nobody — refuse the fiction")
        c["state"] = "closed"
        self.audit.append({"event": "cohort_census_closed",
                           "cohort": cohort_id, "members": len(c["members"]),
                           "day": day, "by": by})
        return self._public(cohort_id)

    def is_covered(self, cohort_id, member_ref):
        c = self.cohorts.get(cohort_id)
        if c is None:
            return False
        return member_ref in c["members"]   # not-on-list -> not covered

    def invoice(self, cohort_id):
        c = self.cohorts.get(cohort_id)
        if c is None:
            _refuse("unknown cohort")
        n = len(c["members"])
        total = n * c["price_per_member_minor"]
        return {"cohort_id": cohort_id, "members": n,
                "price_per_member_minor": c["price_per_member_minor"],
                "total_minor": total, "currency": c["currency"],
                "invoice_sha256": hashlib.sha256(
                    f"{cohort_id}|{n}|{total}".encode()).hexdigest()}

    def _public(self, cohort_id):
        c = self.cohorts[cohort_id]
        return {k: v for k, v in c.items() if k != "members"} | \
            {"members": len(c["members"])}

    def report(self):
        return {"version": COHORT_VERSION, "cohorts": len(self.cohorts),
                "members": sum(len(c["members"])
                               for c in self.cohorts.values())}
