"""Canonical facility registry (ADR-0057) — answers "does each facility have
a unique identifier?" with a governed YES.

Format: OHF-<state>-<lga>-<seq> (e.g. OHF-LG-IKJ-0004) minted by this
registry only, never hand-typed. External UIDs (DHIS2 org-unit uid, HMIS
code, NHIA provider code) are mapped attributes, not identity — the OHF id
is the spine every other service (claims, QR tags, presence, custody, sync)
references. Duplicate detection: same name+LGA or same DHIS2 uid is a
suspected duplicate, flagged for steward review rather than silently merged.
"""
from __future__ import annotations
import json, re, sys

STATE_CODES = {"lagos": "LG", "kano": "KN", "kaduna": "KD", "rivers": "RV", "oyo": "OY",
               "abuja": "FC", "borno": "BO", "enugu": "EN", "anambra": "AN", "edo": "ED"}

class RegistryError(Exception):
    pass
class DuplicateSuspect(RegistryError):
    pass

def _slug(s: str, n=3) -> str:
    s = re.sub(r"[^a-z]", "", s.lower())
    if len(s) < n:
        raise RegistryError(f"cannot derive code from '{s}'")
    return s[:n].upper()

class FacilityRegistry:
    def __init__(self):
        self.by_id = {}
        self.by_dhis2 = {}
        self._seq = {}
        self.duplicate_flags = []

    def mint(self, name: str, state: str, lga: str, ftype: str,
             dhis2_uid: str | None = None, hmis_code: str | None = None) -> dict:
        sc = STATE_CODES.get(state.lower(), _slug(state))
        lc = _slug(lga)
        if dhis2_uid and dhis2_uid in self.by_dhis2:
            raise DuplicateSuspect(f"dhis2 uid {dhis2_uid} already registered as {self.by_dhis2[dhis2_uid]}")
        # same name + lga => suspect (flag, still mint — steward adjudicates)
        for f in self.by_id.values():
            if f["name"].lower() == name.lower() and f["lga"].lower() == lga.lower():
                self.duplicate_flags.append({"candidate": name, "existing": f["facility_id"]})
        key = f"{sc}-{lc}"
        self._seq[key] = self._seq.get(key, 0) + 1
        fid = f"OHF-{sc}-{lc}-{self._seq[key]:04d}"
        rec = {"facility_id": fid, "name": name, "state": state, "lga": lga,
               "type": ftype, "dhis2_uid": dhis2_uid, "hmis_code": hmis_code,
               "status": "active"}
        self.by_id[fid] = rec
        if dhis2_uid:
            self.by_dhis2[dhis2_uid] = fid
        return rec

    def get(self, fid: str) -> dict | None:
        return self.by_id.get(fid)

    def resolve_external(self, dhis2_uid: str) -> str | None:
        return self.by_dhis2.get(dhis2_uid)

    def import_dhis2_orgunits(self, units: list[dict]) -> dict:
        """Inbound sync (ADR-0057): DHIS2 org units become registered
        facilities; existing uid mappings are updated, not duplicated."""
        created = updated = 0
        for u in units:
            uid = u.get("id")
            if uid in self.by_dhis2:
                f = self.by_id[self.by_dhis2[uid]]
                f["name"] = u.get("name", f["name"])
                updated += 1
            else:
                self.mint(u["name"], u.get("state", "unknown"), u.get("lga", "unknown"),
                          u.get("type", "unknown"), dhis2_uid=uid)
                created += 1
        return {"created": created, "updated": updated}

def main(argv):
    reg = FacilityRegistry()
    doc = json.load(sys.stdin)
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "import-dhis2":
        print(json.dumps({**reg.import_dhis2_orgunits(doc.get("org_units", [])),
                          "facilities": list(reg.by_id.values())}))
        return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
