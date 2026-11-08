"""vhreinsure.py — reinsurance, reserving, and solvency for the scheme.

The fund has buckets and a backstop; a regulator's first question is
harder: what happens when the catastrophe lands? This module answers
in entries and arithmetic, not adjectives:

- TREATIES ARE RATIFIED ENTRIES: quota-share and excess-of-loss
  treaties with a named reinsurer are proposed and ratified four-eyes;
  ceding to an unratified treaty refuses.
- RECOVERIES ARE COMPUTED, STATED: cession percentages and
  attachment/limit arithmetic per claim, with the treaty terms echoed
  on every recovery.
- IBNR USES DECLARED DEVELOPMENT FACTORS: incurred-but-not-reported
  reserving refuses to guess — factors are ratified entries, and a
  reserve computed without them refuses, never estimates silently.
- SOLVENCY IS A RATIO WITH A FLOOR: required capital is a declared
  percentage of written premium plus reserves; below the floor the
  report says BREACH, stated, not amber.

Doctrine carried: mixed-currency arithmetic refuses (amounts arrive
in EUR minor, revalued upstream by vhtreasury); absence is not a
value; the hand that proposes never ratifies.
"""

import hashlib
import json

VHREINSURE_VERSION = "vhreinsure@1.0.0"


class ReinsureError(Exception):
    pass


def _refuse(msg):
    raise ReinsureError(msg)


class TreatyRegistry:
    """Reinsurance treaties — four-eyes ratified entries."""

    KINDS = ("quota-share", "excess-of-loss")

    def __init__(self):
        self.treaties = {}
        self.audit = []

    def propose(self, treaty_id, definition, *, by, day):
        if treaty_id in self.treaties:
            _refuse(f"treaty {treaty_id} exists; renew as a new "
                    "entry, never edit in place")
        kind = definition.get("kind")
        if kind not in self.KINDS:
            _refuse(f"unknown treaty kind {kind!r}; kinds are "
                    f"{self.KINDS}")
        if not definition.get("reinsurer"):
            _refuse("a treaty names its reinsurer")
        if kind == "quota-share":
            cession = definition.get("cession_pct")
            if not cession or not (0 < cession <= 100):
                _refuse("a quota-share declares cession_pct in "
                        "(0, 100]")
        else:
            if not definition.get("attachment_eur_minor") or \
                    not definition.get("limit_eur_minor"):
                _refuse("an excess-of-loss declares attachment and "
                        "limit in EUR minor")
        self.treaties[treaty_id] = {**definition, "status": "proposed",
                                    "proposed_by": by}
        self.audit.append({"op": "propose", "treaty": treaty_id,
                           "by": by, "day": day})

    def ratify(self, treaty_id, *, by, day):
        t = self.treaties.get(treaty_id)
        if not t or t["status"] != "proposed":
            _refuse(f"no proposed treaty {treaty_id}")
        if by == t["proposed_by"]:
            _refuse("the hand that proposes never ratifies")
        t["status"] = "ratified"
        t["ratified_by"] = by
        self.audit.append({"op": "ratify", "treaty": treaty_id,
                           "by": by, "day": day})
        return t

    def live(self, treaty_id):
        t = self.treaties.get(treaty_id)
        if not t or t["status"] != "ratified":
            _refuse(f"treaty {treaty_id} is not ratified — ceding to "
                    "an unratified treaty refuses")
        return t


class FactorRegistry:
    """IBNR development factors — ratified, never estimated."""

    def __init__(self):
        self.factors = {}        # line -> {factor, status...}

    def propose(self, line, factor, *, by, day, basis=None):
        if factor < 1.0:
            _refuse("a development factor below 1.0 claims claims "
                    "shrink with time — refused without a miracle")
        if not basis:
            _refuse("a development factor states its basis (triangles"
                    " of which years?)")
        self.factors[line] = {"factor": factor, "basis": basis,
                              "status": "proposed", "proposed_by": by}

    def ratify(self, line, *, by, day):
        f = self.factors.get(line)
        if not f or f["status"] != "proposed":
            _refuse(f"no proposed factor for {line}")
        if by == f["proposed_by"]:
            _refuse("the hand that proposes never ratifies")
        f["status"] = "ratified"
        f["ratified_by"] = by
        return f

    def live(self, line):
        f = self.factors.get(line)
        if not f or f["status"] != "ratified":
            _refuse(f"no ratified development factor for {line} — "
                    "IBNR without factors refuses, never guesses")
        return f["factor"]


class ReinsuranceLedger:
    """Cessions, recoveries, reserves, and the solvency ratio."""

    def __init__(self, *, treaties=None, factors=None):
        self.treaties = treaties or TreatyRegistry()
        self.factors = factors or FactorRegistry()
        self.cessions = []       # per-claim ceded amounts
        self.claims = []         # {claim_ref, line, gross, paid}

    def register_claim(self, claim_ref, *, line, gross_eur_minor,
                       paid_eur_minor=0, day):
        if gross_eur_minor < 0 or paid_eur_minor < 0:
            _refuse("claim amounts are non-negative minor units")
        if paid_eur_minor > gross_eur_minor:
            _refuse("paid cannot exceed gross — an overpaid claim is "
                    "an incident, not an entry")
        self.claims.append({"claim": claim_ref, "line": line,
                            "gross": gross_eur_minor,
                            "paid": paid_eur_minor, "day": day})

    def cede(self, treaty_id, *, claim_ref=None, line=None):
        """Compute recoveries under a ratified treaty. Quota-share
        cedes a percentage of every claim on the line; excess-of-loss
        recovers the layer above attachment, capped at limit."""
        t = self.treaties.live(treaty_id)
        scope = [c for c in self.claims
                 if (claim_ref is None or c["claim"] == claim_ref)
                 and (line is None or c["line"] == line)]
        if not scope:
            _refuse("no claims in scope — ceding nothing is stated, "
                    "not zeroed")
        recoveries = []
        for c in scope:
            if t["kind"] == "quota-share":
                recovered = int(round(
                    c["gross"] * t["cession_pct"] / 100))
            else:
                above = max(0, c["gross"] - t["attachment_eur_minor"])
                recovered = min(above, t["limit_eur_minor"])
            rec = {"claim": c["claim"], "treaty": treaty_id,
                   "kind": t["kind"], "reinsurer": t["reinsurer"],
                   "gross_eur_minor": c["gross"],
                   "recovered_eur_minor": recovered,
                   "terms": {k: t[k] for k in
                             ("cession_pct", "attachment_eur_minor",
                              "limit_eur_minor") if k in t}}
            self.cessions.append(rec)
            recoveries.append(rec)
        return recoveries

    def ibnr_reserve(self, line, *, as_of_day):
        """IBNR = (gross − paid) × (factor − 1). The factor is
        ratified; without it this refuses."""
        factor = self.factors.live(line)
        scope = [c for c in self.claims
                 if c["line"] == line and c["day"] <= as_of_day]
        if not scope:
            return {"line": line, "ibnr_eur_minor": 0,
                    "claims": 0,
                    "reason": "no claims on this line in scope — "
                              "stated, not zeroed"}
        outstanding = sum(c["gross"] - c["paid"] for c in scope)
        ibnr = int(round(outstanding * (factor - 1)))
        return {"line": line, "claims": len(scope),
                "outstanding_eur_minor": outstanding,
                "development_factor": factor,
                "ibnr_eur_minor": ibnr, "as_of_day": as_of_day}

    def solvency_report(self, *, written_premium_eur_minor,
                        available_capital_eur_minor,
                        required_pct_of_premium, lines, as_of_day):
        """Required = declared % of written premium + total IBNR.
        Below 1.0 is a breach — stated, never amber."""
        if not (0 < required_pct_of_premium <= 100):
            _refuse("the required-capital percentage is declared in "
                    "(0, 100]")
        if available_capital_eur_minor < 0:
            _refuse("capital is a non-negative figure")
        ibnr_total = 0
        per_line = []
        for line in lines:
            r = self.ibnr_reserve(line, as_of_day=as_of_day)
            ibnr_total += r["ibnr_eur_minor"]
            per_line.append(r)
        required = int(round(
            written_premium_eur_minor * required_pct_of_premium / 100
        )) + ibnr_total
        ratio = (round(available_capital_eur_minor / required, 4)
                 if required else None)
        rep = {"version": VHREINSURE_VERSION,
               "written_premium_eur_minor": written_premium_eur_minor,
               "ibnr_eur_minor": ibnr_total,
               "per_line": per_line,
               "required_capital_eur_minor": required,
               "available_capital_eur_minor":
                   available_capital_eur_minor,
               "solvency_ratio": ratio,
               "state": ("solvent" if ratio is not None and ratio >= 1
                         else "BREACH — required capital exceeds "
                              "available; stated, not amber")}
        canon = json.dumps(rep, sort_keys=True).encode()
        rep["sha256"] = hashlib.sha256(canon).hexdigest()
        return rep
