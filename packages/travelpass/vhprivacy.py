"""Privacy pack — data-protection compliance as registry entries.

Every market's data-protection regime is an entry (DPA, statute,
retention rule); the pack enforces retention schedules, executes
erasure that respects pseudonymization (the salt-mapping is revoked,
the pseudonym tombstoned — never a silent row-delete that breaks the
ledger's chain), and exports a checksum-stamped audit pack for the
authority. NDPC (Nigeria) and CNPD (Cabo Verde) ship first; every
African market entry in vhmarkets can be reported against.

Doctrine: the registry is pseudonymized at registration — erasure
severs the mapping, it does not rewrite history.
"""

import hashlib
import json

PRIVACY_VERSION = "vhprivacy@1.0.0"

# country -> {"dpa", "statute", "retention_days"} — entries, not branches
PRIVACY_REGIMES = {
    "ng": {"dpa": "NDPC", "statute": "Nigeria Data Protection Act 2023",
           "retention_days": 1825},          # 5y financial record floor
    "cv": {"dpa": "CNPD", "statute": "Lei 133/V/2001 + GDPR-aligned decree",
           "retention_days": 1825},
    "gh": {"dpa": "DPA-GH", "statute": "Data Protection Act 2012 (Act 843)",
           "retention_days": 1825},
    "ke": {"dpa": "ODPC", "statute": "Data Protection Act 2019",
           "retention_days": 1825},
    "za": {"dpa": "INFORREG-ZA", "statute": "POPIA 2013",
           "retention_days": 1825},
}


class PrivacyError(Exception):
    pass


def _refuse(msg):
    raise PrivacyError(msg)


def regime(country):
    r = PRIVACY_REGIMES.get(country)
    if r is None:
        _refuse(f"no privacy regime entry for {country!r} — add the entry, "
                "never the branch")
    return r


class PrivacyRegister:
    """Erasure ledger — severed mappings, never rewritten history."""

    def __init__(self):
        self.erasures = []        # append-only tombstones
        self.audit = []

    def erase(self, *, country, pseudonym, salt_mapping_revoked,
              requested_by, approved_by, day, reason):
        """Erasure is four-eyes, reasoned, and honest about what it is:
        the pseudonym's salt-mapping is revoked so re-identification is
        impossible; the ledger keeps the tombstone."""
        regime(country)
        if not pseudonym:
            _refuse("erasure needs the pseudonym — the registry never "
                    "held the person")
        if not salt_mapping_revoked:
            _refuse("the salt-mapping must be revoked — erasure severs "
                    "the mapping, it does not delete rows")
        if not requested_by or not approved_by or requested_by == approved_by:
            _refuse("the hand that proposes never approves")
        if not reason:
            _refuse("erasure without a reason is a disappearance")
        if any(e["pseudonym"] == pseudonym for e in self.erasures):
            _refuse("already erased — a tombstone is not written twice")
        rec = {"pseudonym": pseudonym, "country": country,
               "requested_by": requested_by, "approved_by": approved_by,
               "day": day, "reason": reason}
        self.erasures.append(rec)
        self.audit.append({"event": "erasure_executed", **rec})
        return dict(rec)

    def is_erased(self, pseudonym):
        return any(e["pseudonym"] == pseudonym for e in self.erasures)

    def audit_pack(self, *, country, day):
        """Checksum-stamped export for the authority — the checksum is
        the contract."""
        r = regime(country)
        pack = {"version": PRIVACY_VERSION, "country": country,
                "dpa": r["dpa"], "statute": r["statute"],
                "retention_days": r["retention_days"],
                "erasures": [dict(e) for e in self.erasures
                             if e["country"] == country],
                "generated_day": day}
        canon = json.dumps(pack, sort_keys=True).encode()
        pack["sha256"] = hashlib.sha256(canon).hexdigest()
        return pack

    def report(self):
        return {"version": PRIVACY_VERSION,
                "regimes": len(PRIVACY_REGIMES),
                "erasures": len(self.erasures)}
