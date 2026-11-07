"""Operator registry — the company-agnostic layer.

The platform is not one insurer. An operator (insurer, scheme
administrator, TPA, government scheme) is an ENTRY in this registry:
it names its legal self, its market, its settlement currency, and the
country profiles it is licensed to sell. Pricing power is bounded —
an operator ratifies profile prices within stated floors/ceilings, and
ratification is four-eyes: the hand that proposes never approves.

Unknown operator: refuse. Operator selling outside its licensed
profiles: refuse. Pricing outside bounds: refuse. The door is the
registry.
"""

import hashlib

from vhmarkets import market as _market, currency as _currency

OPERATORS_VERSION = "vhoperators@1.0.0"


class OperatorError(Exception):
    pass


def _refuse(msg):
    raise OperatorError(msg)


class OperatorRegistry:
    def __init__(self):
        self.operators = {}
        self.audit = []

    def register(self, operator_id, *, legal_name, country,
                 settlement_currency, allowed_profiles, brand=None,
                 pricing_bounds=None, day):
        if not operator_id or not legal_name:
            _refuse("an operator names its legal self")
        if operator_id in self.operators:
            _refuse("duplicate operator id")
        _market(country)                      # unknown market refuses
        _currency(settlement_currency)        # unknown currency refuses
        if not allowed_profiles:
            _refuse("an operator with no licensed profiles sells nothing")
        self.operators[operator_id] = {
            "operator_id": operator_id, "legal_name": legal_name,
            "country": country,
            "settlement_currency": settlement_currency,
            "allowed_profiles": tuple(allowed_profiles),
            "brand": brand or {},
            # {"profile_id": {"floor_minor": int, "ceiling_minor": int}}
            "pricing_bounds": dict(pricing_bounds or {}),
            "ratified_prices": {},            # profile -> price record
            "state": "active", "registered_day": day,
        }
        self.audit.append({"event": "operator_registered",
                           "operator": operator_id, "day": day})
        return self._public(operator_id)

    def _op(self, operator_id):
        op = self.operators.get(operator_id)
        if op is None:
            _refuse(f"unknown operator {operator_id!r} — the door is the registry")
        if op["state"] != "active":
            _refuse("operator not active")
        return op

    def suspend(self, operator_id, *, reason, day):
        if not reason:
            _refuse("a suspension without a reason is a disappearance")
        op = self._op(operator_id)
        op["state"] = "suspended"
        self.audit.append({"event": "operator_suspended",
                           "operator": operator_id, "reason": reason,
                           "day": day})
        return self._public(operator_id)

    def ratify_price(self, operator_id, profile_id, *, price_minor,
                     proposed_by, approved_by, day):
        """Four-eyes price ratification inside the stated bounds — a
        price not ratified is not a price."""
        op = self._op(operator_id)
        if profile_id not in op["allowed_profiles"]:
            _refuse("operator not licensed for this profile")
        if not proposed_by or not approved_by or proposed_by == approved_by:
            _refuse("the hand that proposes never approves")
        bounds = op["pricing_bounds"].get(profile_id)
        if bounds:
            lo, hi = bounds["floor_minor"], bounds["ceiling_minor"]
            if not (lo <= price_minor <= hi):
                _refuse(f"price {price_minor} outside ratified band "
                        f"[{lo}, {hi}] — the band is the mandate")
        rec = {"profile_id": profile_id, "price_minor": price_minor,
               "proposed_by": proposed_by, "approved_by": approved_by,
               "day": day}
        op["ratified_prices"][profile_id] = rec
        self.audit.append({"event": "price_ratified",
                           "operator": operator_id, **rec})
        return dict(rec)

    def price_for(self, operator_id, profile_id):
        """The operator's ratified price for a profile, or refuse —
        absence is not a price."""
        op = self._op(operator_id)
        rec = op["ratified_prices"].get(profile_id)
        if rec is None:
            _refuse("no ratified price — a price not ratified is not a price")
        return rec["price_minor"]

    def assert_sellable(self, operator_id, profile_id):
        op = self._op(operator_id)
        if profile_id not in op["allowed_profiles"]:
            _refuse("operator not licensed for this profile — "
                    "the licence is the boundary")
        return op

    def audit_digest(self):
        canon = "|".join(
            f"{a['event']}:{a.get('operator')}:{a['day']}"
            for a in self.audit)
        return hashlib.sha256(canon.encode()).hexdigest()

    def _public(self, operator_id):
        return {k: v for k, v in self.operators[operator_id].items()}

    def report(self):
        return {"version": OPERATORS_VERSION,
                "operators": len(self.operators),
                "active": sum(1 for o in self.operators.values()
                              if o["state"] == "active"),
                "audit_sha256": self.audit_digest()}
