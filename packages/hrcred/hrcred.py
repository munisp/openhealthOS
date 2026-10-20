"""Staff registry + professional credentialing (ADR-0058).

Tracks licensure (MDCN for physicians, NMCN for nurses/midwives, PCN for
pharmacists, MLSCN for lab scientists), expiry, and role scopes. Two platform
consumers:
  - CPOE prescriber gate: check_prescriber() must pass before orders place.
  - QR verification-key provisioning: qr_key_eligible() gates which staff
    devices the MDM channel may provision (ADR-0055 doctrine).
Fail-soft on registry outages is the CALLER's job; this module is pure.
"""
from __future__ import annotations
import json, sys, time

COUNCILS = {"MDCN": "physician", "NMCN": "nurse-midwife", "PCN": "pharmacist",
            "MLSCN": "lab-scientist", "CHPRBN": "chw"}
# roles allowed to prescribe / to hold QR verification keys
PRESCRIBING_ROLES = {"physician"}
QR_KEY_ROLES = {"physician", "nurse-midwife", "pharmacist", "chw"}

class CredentialError(Exception):
    pass

def _parse_council(licence):
    if not licence or "-" not in licence:
        raise CredentialError(f"malformed licence '{licence}' (expect COUNCIL-number)")
    council = licence.split("-", 1)[0].upper()
    if council not in COUNCILS:
        raise CredentialError(f"unknown council '{council}'")
    return council

class StaffRegistry:
    def __init__(self, now=None):
        self.now = now or (lambda: int(time.time()))
        self.staff = {}

    def register(self, staff_id, name, licence, expires_at, facility_id):
        council = _parse_council(licence)
        rec = {"staff_id": staff_id, "name": name, "licence": licence,
               "council": council, "role": COUNCILS[council],
               "expires_at": int(expires_at), "facility_id": facility_id,
               "suspended": False}
        self.staff[staff_id] = rec
        return rec

    def suspend(self, staff_id):
        self.staff[staff_id]["suspended"] = True

    def _valid(self, staff_id):
        r = self.staff.get(staff_id)
        if not r or r["suspended"] or r["expires_at"] <= self.now():
            return None
        return r

    def check_prescriber(self, staff_id):
        r = self._valid(staff_id)
        return bool(r and r["role"] in PRESCRIBING_ROLES)

    def qr_key_eligible(self, staff_id):
        r = self._valid(staff_id)
        return bool(r and r["role"] in QR_KEY_ROLES)

    def expiring(self, within_days=30):
        horizon = self.now() + within_days * 86400
        return [r for r in self.staff.values()
                if not r["suspended"] and self.now() < r["expires_at"] <= horizon]

    def expired(self):
        return [r for r in self.staff.values()
                if not r["suspended"] and r["expires_at"] <= self.now()]

def main(argv):
    if len(argv) < 2:
        print(json.dumps({"error": "usage: hrcred.py check-prescriber|qr-eligible"}))
        return 1
    doc = json.load(sys.stdin)
    reg = StaffRegistry()
    for s in doc.get("staff", []):
        reg.register(s["staff_id"], s["name"], s["licence"], s["expires_at"], s["facility_id"])
    sid = doc.get("staff_id", "")
    if argv[1] == "check-prescriber":
        print(json.dumps({"ok": reg.check_prescriber(sid)})); return 0
    if argv[1] == "qr-eligible":
        print(json.dumps({"ok": reg.qr_key_eligible(sid)})); return 0
    if argv[1] == "expiring":
        print(json.dumps({"expiring": reg.expiring(doc.get("within_days", 30))})); return 0
    return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
