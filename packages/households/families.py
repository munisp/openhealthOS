#!/usr/bin/env python3
"""Families — how the platform groups a family together (ADR-0022).

The household register (households.py) groups people by WHERE they
live. This module groups them by WHO they are to each other — the
kinship layer Nigerian primary care actually works through:

  - a family has one head/guardian and members with relationship codes
    (spouse, child, parent, sibling, other-dependant);
  - the guardian is the CONTACT POINT: a child's immunization reminder,
    a mother's ANC reminder, a dispute receipt — they go to the
    guardian's phone, which is the family's shared phone in most PHC
    reality;
  - families link to households (geography) and to payer enrolment
    (one premium can cover the family unit — the scheme decides, the
    link is recorded here);
  - guardian-aware defaulter tracing: instead of "five children due",
    the CHW gets "visit the Bello family — three children due", one
    trip instead of five.

Privacy: the register holds patient ids and relationship codes; names
and phones stay at the facility/CHW tier, matching the households
privacy split.
"""
from __future__ import annotations

from dataclasses import dataclass, field

RELATIONSHIPS = ("head", "spouse", "child", "parent", "sibling", "other-dependant")

# Relationship codes that make someone a dependant whose reminders
# route to the guardian.
DEPENDANTS = {"child", "other-dependant"}


class FamilyError(Exception):
    pass


@dataclass
class FamilyMember:
    patient_id: str
    relationship: str          # one of RELATIONSHIPS
    guardian_id: str = ""      # for dependants: who answers for them
    birth_year: int = 0        # for under-5 counts; 0 unknown


@dataclass
class Family:
    family_id: str
    head_id: str               # patient id of the head/guardian
    household_id: str = ""     # link to the GIS register (optional)
    shared_phone: str = ""     # family contact point (stays at CHW tier)
    scheme: str = ""           # payer enrolment link (family cover)
    members: list = field(default_factory=list)  # FamilyMember

    def dependants(self) -> list:
        return [m for m in self.members if m.relationship in DEPENDANTS]

    def children_under(self, years: int, current_year: int) -> list:
        return [m for m in self.members
                if m.relationship == "child" and m.birth_year
                and current_year - m.birth_year < years]


class FamilyRegister:
    def __init__(self):
        self._families: dict[str, Family] = {}
        self._by_patient: dict[str, str] = {}  # patient -> family

    def create(self, family: Family, actor: str) -> None:
        if not family.family_id or not family.head_id:
            raise FamilyError("family_id and head_id required")
        if not actor:
            raise FamilyError("actor required — family records name who made them")
        if family.family_id in self._families:
            raise FamilyError("family already exists")
        self._families[family.family_id] = family
        self._by_patient[family.head_id] = family.family_id
        # the head is implicitly a member
        family.members.insert(0, FamilyMember(family.head_id, "head"))

    def get(self, family_id: str) -> Family | None:
        return self._families.get(family_id)

    def family_of(self, patient_id: str) -> Family | None:
        fid = self._by_patient.get(patient_id)
        return self._families.get(fid) if fid else None

    def add_member(self, family_id: str, member: FamilyMember, actor: str) -> None:
        if not actor:
            raise FamilyError("actor required")
        f = self._families.get(family_id)
        if not f:
            raise FamilyError("unknown family")
        if member.relationship not in RELATIONSHIPS:
            raise FamilyError(f"relationship must be one of {RELATIONSHIPS}")
        if member.relationship == "head":
            raise FamilyError("a family has one head — transfer headship instead")
        if member.patient_id in self._by_patient:
            raise FamilyError("patient already belongs to a family")
        if member.relationship in DEPENDANTS and not member.guardian_id:
            member.guardian_id = f.head_id  # default: the head answers for them
        f.members.append(member)
        self._by_patient[member.patient_id] = family_id

    def contact_for(self, patient_id: str) -> str:
        """Who the platform messages about this patient: dependants'
        reminders route to their guardian's contact — the family phone."""
        f = self.family_of(patient_id)
        if not f:
            return patient_id  # standalone patient: messaged directly
        for m in f.members:
            if m.patient_id == patient_id:
                if m.relationship in DEPENDANTS:
                    return m.guardian_id or f.head_id
                return patient_id
        return patient_id

    def guardian_worklist(self, due: list) -> list:
        """Guardian-aware defaulter tracing. `due` is a list of
        (patient_id, due_service, due_date) tuples — e.g. from
        households.defaulters(). Output: one work item per family per
        guardian, grouping every dependant due under the person to
        visit or call."""
        # one work item per family (or per standalone patient): the
        # visit happens once; each entry names who to contact for that
        # patient (children -> guardian, adults -> themselves).
        grouped: dict[str, dict] = {}
        for patient_id, service, due_date in due:
            f = self.family_of(patient_id)
            contact = self.contact_for(patient_id)
            key = f.family_id if f else f"solo:{patient_id}"
            g = grouped.setdefault(key, {
                "family_id": f.family_id if f else None,
                "household_id": f.household_id if f else "",
                "shared_phone": f.shared_phone if f else "",
                "patients_due": []})
            g["patients_due"].append({"patient_id": patient_id, "contact": contact,
                                      "service": service, "due": due_date})
        return sorted(grouped.values(),
                      key=lambda g: -len(g["patients_due"]))

    def stats(self) -> dict:
        fams = list(self._families.values())
        return {
            "families": len(fams),
            "members": sum(len(f.members) for f in fams),
            "dependants": sum(len(f.dependants()) for f in fams),
            "with_shared_phone": sum(1 for f in fams if f.shared_phone),
            "with_scheme": sum(1 for f in fams if f.scheme),
        }
