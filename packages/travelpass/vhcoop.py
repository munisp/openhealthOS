"""vhcoop.py — the insurer-grade data cooperative.

vhextract sells anonymized actuarial extracts; this module is the
COOPERATIVE around the shelf: members (the operators whose claims
history feeds the marts) hold declared revenue shares, and every sale
splits its proceeds back to them and to the scheme fund — by ratified
share table, in the sale's own currency, with mixed-currency sums
refusing like everywhere else.

Doctrine:
- MEMBERSHIP AND SHARES ARE RATIFIED: an operator joins and takes a
  share by four-eyes entry; shares sum to exactly 100 or the table
  refuses to activate (fund doctrine carried).
- PROCEEDS SPLIT IN THE SALE'S CURRENCY: no silent conversion — an
  NGN sale distributes NGN; vhtreasury owns revaluation.
- THE FUND'S CUT IS DECLARED: the scheme fund holds a named share
  line like any member; nothing leaks to "the platform" untracked.
- EVERY DISTRIBUTION IS A CHECKSUMMED STATEMENT: members can audit
  their cut against the sale registry.
"""

import hashlib
import json

VHCOOP_VERSION = "vhcoop@1.0.0"


class CoopError(Exception):
    pass


def _refuse(msg):
    raise CoopError(msg)


class CoopRegistry:
    """Members and their revenue shares — four-eyes entries."""

    def __init__(self):
        self.members = {}          # member_id -> {share_pct, status}
        self._pending_table = None
        self.active = False
        self.audit = []

    def propose_table(self, shares, *, by, day):
        """shares: {member_id: pct} — must sum to exactly 100."""
        if not shares:
            _refuse("an empty share table divides nothing")
        total = sum(shares.values())
        if total != 100:
            _refuse(f"shares sum to {total}, not 100 — the table is "
                    "the contract")
        if any(v <= 0 for v in shares.values()):
            _refuse("a member's share is a positive percentage")
        self._pending_table = {"shares": dict(shares),
                               "proposed_by": by, "proposed_day": day}
        self.audit.append({"op": "propose-table", "by": by,
                           "day": day})

    def ratify_table(self, *, by, day):
        p = self._pending_table
        if not p:
            _refuse("no proposed table to ratify")
        if by == p["proposed_by"]:
            _refuse("the hand that proposes never ratifies")
        for member_id, pct in p["shares"].items():
            self.members[member_id] = {
                "share_pct": pct, "status": "active",
                "ratified_by": by, "ratified_day": day}
        self.active = True
        self.audit.append({"op": "ratify-table", "by": by,
                           "day": day})
        return {m: dict(v) for m, v in self.members.items()}


class CoopDesk:
    """Splits extract-sale proceeds by the ratified table."""

    def __init__(self, registry, extract_desk):
        self.registry = registry
        self.extracts = extract_desk
        self.distributions = []

    def distribute(self, *, from_day, to_day, day):
        """Settle one window of sales. Mixed currencies are reported
        per-currency, never summed across."""
        if not self.registry.active:
            _refuse("no ratified share table — a cooperative without "
                    "a contract distributes nothing")
        sales = [s for s in self.extracts.sales
                 if from_day <= s["day"] <= to_day]
        if not sales:
            _refuse("no sales in the window — stated, not zeroed")
        by_currency = {}
        for s in sales:
            by_currency.setdefault(s["currency"], 0)
            by_currency[s["currency"]] += s["price_minor"]
        lines = []
        for cur, gross in sorted(by_currency.items()):
            for member_id, m in sorted(self.registry.members.items()):
                cut = int(round(gross * m["share_pct"] / 100))
                lines.append({"member": member_id, "currency": cur,
                              "gross_minor": gross,
                              "share_pct": m["share_pct"],
                              "cut_minor": cut})
        stmt = {"version": VHCOOP_VERSION,
                "window": [from_day, to_day],
                "sales": len(sales), "lines": lines, "day": day}
        canon = json.dumps(stmt, sort_keys=True).encode()
        stmt["sha256"] = hashlib.sha256(canon).hexdigest()
        self.distributions.append(stmt)
        return stmt

    def member_statement(self, member_id):
        """One member's cumulative cuts — auditable against sales."""
        if member_id not in self.registry.members:
            _refuse(f"{member_id} is not a cooperative member")
        cuts = {}
        for stmt in self.distributions:
            for ln in stmt["lines"]:
                if ln["member"] == member_id:
                    cuts.setdefault(ln["currency"], 0)
                    cuts[ln["currency"]] += ln["cut_minor"]
        return {"member": member_id,
                "share_pct":
                    self.registry.members[member_id]["share_pct"],
                "cuts_minor_by_currency": cuts,
                "distributions": len(self.distributions)}
