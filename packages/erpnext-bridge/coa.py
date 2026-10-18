#!/usr/bin/env python3
"""Chart-of-accounts mapping (ADR-0015) — the ERP row's missing half.

The bridge posts journal entries to ERPNext, but a journal entry is only
as good as the account names on its legs. Platform ledger accounts are
operational codes (SETTLE-POOL, WALLET-<facility>, PSP-FLOAT); the
finance office's books speak GL accounts (Scheme Settlement Pool - OHN).
The mapping between them is *configuration* — it changes per state
deployment, per company suffix, per ERPNext chart — so it lives in a
bundle (bundles/coa-ng-default.json), never in code.

Three guarantees:

  1. Deterministic mapping: exact keys match whole codes; keys ending in
     "-*" are prefix rules for parameterized accounts. An unmapped code
     is a hard error — the journal never posts with platform codes
     leaking into the finance books.
  2. Bundle validation: the bundle must be well-formed (company, suffix,
     non-empty accounts, no duplicate effective targets with different
     case, account names end with the declared suffix) — config errors
     fail at load, not mid-flush.
  3. Remote validation: before trusting a bundle against a live ERPNext,
     validate_remote checks every mapped account actually exists on the
     server — a typo'd GL account name surfaces as a config error at
     startup, not as a rejected journal at month-end.
"""
from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass


class UnmappedAccount(ValueError):
    """A platform account code with no mapping rule in the bundle."""


class InvalidBundle(ValueError):
    """The COA bundle is malformed."""


@dataclass
class COAMapping:
    company: str
    suffix: str
    accounts: dict[str, str]          # exact or "-*"-suffixed prefix rules

    def map_account(self, code: str) -> str:
        """Platform ledger account code → ERPNext GL account name.
        Exact rules win over prefix rules; longest prefix wins among
        prefix rules. Unmapped codes raise UnmappedAccount."""
        if code in self.accounts:
            return self.accounts[code]
        best: tuple[int, str] | None = None
        for key, target in self.accounts.items():
            if key.endswith("-*"):
                prefix = key[:-1]          # keep the trailing dash: "WALLET-"
                if code.startswith(prefix) and (best is None or len(prefix) > best[0]):
                    best = (len(prefix), target)
        if best is not None:
            return best[1]
        raise UnmappedAccount(f"no COA mapping for platform account {code!r}")

    def apply_to_journal(self, doc: dict) -> dict:
        """Rewrite a Journal Entry doc's account legs in place (mapped)."""
        for leg in doc.get("accounts", []):
            leg["account"] = self.map_account(leg["account"])
        return doc


def load(path: str) -> COAMapping:
    """Load and validate a bundle. Every structural problem raises
    InvalidBundle at load time."""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    company = raw.get("company", "")
    suffix = raw.get("suffix", "")
    accounts = raw.get("accounts")
    if not company:
        raise InvalidBundle("bundle missing company")
    if not suffix:
        raise InvalidBundle("bundle missing suffix")
    if not isinstance(accounts, dict) or not accounts:
        raise InvalidBundle("bundle has no accounts")
    seen_targets: dict[str, str] = {}
    for code, target in accounts.items():
        if not code:
            raise InvalidBundle("empty account code")
        if not isinstance(target, str) or not target:
            raise InvalidBundle(f"account {code!r}: empty target")
        if not target.endswith(" - " + suffix):
            raise InvalidBundle(
                f"account {code!r}: target {target!r} does not end with suffix ' - {suffix}'")
        low = target.lower()
        if low in seen_targets and seen_targets[low] != target:
            raise InvalidBundle(
                f"targets {seen_targets[low]!r} and {target!r} differ only by case")
        seen_targets[low] = target
    return COAMapping(company=company, suffix=suffix, accounts=dict(accounts))


def validate_remote(client, mapping: COAMapping) -> list[str]:
    """Check every mapped GL account exists on the ERPNext server.
    Returns the list of missing account names (empty = bundle valid
    against this server). Needs the client's _request (FrappeClient)."""
    path = ("/api/resource/Account?fields=[\"name\"]&limit_page_length=500&filters="
            + urllib.parse.quote(json.dumps([["company", "=", mapping.company]])))
    out = client._request("GET", path)
    have = {row.get("name", "") for row in out.get("data", [])}
    return sorted({t for t in mapping.accounts.values()} - have)
