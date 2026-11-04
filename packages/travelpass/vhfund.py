"""vhfund.py — the scheme's fund ledger: the money is five monies.
Every premium splits into versioned allocation buckets at the moment it
lands; every spend names its bucket and its purpose; and when the
insurer fails, the backstop mode is a governed act — regulator-notified,
reasoned, and stamped on every entry that follows.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.7):
  - FR-FND-01: fund ledger with allocation buckets (CV profile:
    45% claims+medevac, 25% clinical capacity, 10% surveillance,
    10% platform, 10% prevention — sums to 100 or the table refuses).
  - FR-FND-02 / S-047: insolvency backstop — activation names who,
    why, and the regulator notice reference; in backstop the fund
    keeps paying claims (the traveller's emergency never meets the
    insurer's balance sheet) while the mode is stamped on everything.

Doctrines:
- A bucket in deficit is a stated finding, never an invisible overdraft
  from another bucket — money does not wander.
- The split happens at posting time; a premium that lands unsplit is
  administration money by default, which is how schemes eat claims
  money.
- Backstop without a regulator notice reference is a rumour of
  collapse — refused.
- The ledger is append-only; corrections are reversing entries, not
  erasures.
"""

VHFUND_VERSION = "vhc-fund@1.0.0"

FUND_RULES = {
    "cv-fund@1.0.0": {
        "buckets": {"claims_medevac": 45, "clinical_capacity": 25,
                    "surveillance": 10, "platform": 10,
                    "prevention": 10},
    },
    "ng-fund@1.0.0": {
        "buckets": {"claims_medevac": 50, "clinical_capacity": 20,
                    "surveillance": 10, "platform": 10,
                    "prevention": 10},
    },
}

FUND_MODES = ("normal", "backstop")


class FundError(Exception):
    pass


def _refuse(msg):
    raise FundError(msg)


def _validate(buckets):
    total = sum(buckets.values())
    if total != 100:
        _refuse("allocation buckets must sum to 100 — got %d; a scheme "
                "that allocates 99%% has a 1%% leak" % total)


for _v in FUND_RULES.values():
    _validate(_v["buckets"])


class FundLedger:
    def __init__(self, *, rules_version="cv-fund@1.0.0"):
        if rules_version not in FUND_RULES:
            _refuse("unknown fund rules version")
        self.rules = dict(FUND_RULES[rules_version])
        self.rules_version = rules_version
        _validate(self.rules["buckets"])
        self.entries = []       # append-only: splits, spends, reversals
        self.mode = "normal"
        self.backstop = None
        self.audit = []

    # --- FR-FND-01: allocation at posting time -------------------------
    def post_premium(self, ref, *, amount_minor, day):
        """A premium lands as five entries — the split is arithmetic at
        the door, with the remainder (rounding dust) landing in claims,
        never vanishing."""
        if amount_minor is None or amount_minor <= 0:
            _refuse("a premium post carries an amount")
        buckets = self.rules["buckets"]
        allocated, rows = 0, []
        items = sorted(buckets.items(), key=lambda kv: -kv[1])
        for bucket, pct in items:
            share = amount_minor * pct // 100
            allocated += share
            rows.append((bucket, share))
        dust = amount_minor - allocated
        if dust:
            rows[0] = (rows[0][0], rows[0][1] + dust)
        for bucket, share in rows:
            self.entries.append({"kind": "allocation", "ref": ref,
                                 "bucket": bucket, "amount_minor": share,
                                 "day": day, "mode": self.mode})
        self.audit.append({"event": "premium_allocated", "ref": ref,
                           "amount_minor": amount_minor, "day": day,
                           "mode": self.mode})
        return {"ref": ref, "allocated": dict(rows)}

    def spend(self, bucket, *, amount_minor, purpose, day, ref=None):
        """A spend names its bucket and purpose. Overspending a bucket
        refuses — a bucket in deficit is a finding to report, not a
        hole to hide in another bucket."""
        if bucket not in self.rules["buckets"]:
            _refuse("unknown bucket — the money is five monies, name "
                    "one")
        if amount_minor is None or amount_minor <= 0:
            _refuse("a spend carries an amount")
        if not purpose:
            _refuse("a spend without a purpose is a withdrawal slip")
        balance = self._balance(bucket)
        if amount_minor > balance:
            _refuse("bucket %s holds %d, spend of %d refuses — report "
                    "the deficit, do not wander into another bucket"
                    % (bucket, balance, amount_minor))
        self.entries.append({"kind": "spend", "ref": ref,
                             "bucket": bucket,
                             "amount_minor": -amount_minor,
                             "purpose": purpose, "day": day,
                             "mode": self.mode})
        self.audit.append({"event": "bucket_spend", "bucket": bucket,
                           "amount_minor": amount_minor, "day": day,
                           "mode": self.mode})
        return {"bucket": bucket, "spent_minor": amount_minor,
                "balance_minor": self._balance(bucket)}

    # --- FR-FND-02: insolvency backstop --------------------------------
    def activate_backstop(self, *, by, reason, notice_ref, day):
        """The insurer failed; the fund keeps paying. Activation is a
        governed act: who, why, and the regulator notice reference —
        without it, 'backstop' is a rumour of collapse."""
        if self.mode == "backstop":
            _refuse("backstop already active — a second activation is "
                    "noise over a crisis")
        if not by or not reason or not notice_ref:
            _refuse("backstop activation names who, why, and the "
                    "regulator notice reference")
        self.mode = "backstop"
        self.backstop = {"activated_by": by, "reason": reason,
                         "notice_ref": notice_ref, "day": day}
        self.audit.append({"event": "backstop_activated", "by": by,
                           "notice_ref": notice_ref, "day": day})
        return self.backstop

    def deactivate_backstop(self, *, by, reason, solvency_ref, day):
        if self.mode != "backstop":
            _refuse("no backstop to deactivate")
        if not by or not reason or not solvency_ref:
            _refuse("exit from backstop names who, why, and the "
                    "solvency evidence")
        self.mode = "normal"
        self.backstop["deactivated"] = {"by": by, "reason": reason,
                                        "solvency_ref": solvency_ref,
                                        "day": day}
        self.audit.append({"event": "backstop_deactivated", "by": by,
                           "day": day})
        return self.backstop

    # --- report ---------------------------------------------------------
    def _balance(self, bucket):
        return sum(e["amount_minor"] for e in self.entries
                   if e.get("bucket") == bucket)

    def report(self):
        buckets = {b: self._balance(b) for b in self.rules["buckets"]}
        return {"mode": self.mode,
                "rules_version": self.rules_version,
                "balances_minor": buckets,
                "allocated_minor": sum(
                    e["amount_minor"] for e in self.entries
                    if e["kind"] == "allocation"),
                "spent_minor": -sum(
                    e["amount_minor"] for e in self.entries
                    if e["kind"] == "spend"),
                "entries": len(self.entries),
                "backstop": self.backstop}
