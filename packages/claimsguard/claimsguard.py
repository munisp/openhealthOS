
"""claimsguard.py — pre-authorization SLA tracker.

A patient bleeding in the ED while the insurer 'processes' the
pre-auth is the oldest trick in the claims playbook: delay IS denial.
This module puts a clock on every pre-authorization: declared payer
SLAs, automatic escalation when the clock runs out, and deemed-consent
rules where the law says silence after N hours IS approval.

Doctrine:
- PAYER SLAS ARE REGISTRY ENTRIES: each payer's response window is
  declared and ratified; an unregistered payer gets the default and
  the default is stated.
- SILENCE HAS A VERDICT: at SLA expiry the request auto-escalates;
  where deemed-consent applies, the record says so with the legal
  basis — it never just flips to approved quietly.
- THE CLOCK IS INJECTED: every state change takes `now`; tests replay
  whole weeks deterministically.
"""

VERSION = "claimsguard@1.0.0"

STATES = ("pending", "approved", "denied", "escalated",
          "deemed-approved")
DEFAULT_SLA_HOURS = 4.0
EMERGENCY_SLA_HOURS = 1.0


class GuardError(Exception):
    pass


class PayerRegistry:
    def __init__(self):
        self._payers = {}

    def declare(self, payer, *, sla_hours, deemed_consent_hours=None,
                legal_basis=""):
        if sla_hours <= 0:
            raise GuardError("SLA must be positive")
        if deemed_consent_hours is not None and not legal_basis:
            raise GuardError(
                "deemed-consent without a legal basis is just taking")
        self._payers[payer] = {"sla_hours": sla_hours,
                               "deemed_consent_hours": deemed_consent_hours,
                               "legal_basis": legal_basis}

    def sla(self, payer, emergency=False):
        p = self._payers.get(payer)
        if emergency:
            return EMERGENCY_SLA_HOURS
        return p["sla_hours"] if p else DEFAULT_SLA_HOURS

    def deemed_consent(self, payer):
        p = self._payers.get(payer)
        return (p["deemed_consent_hours"], p["legal_basis"]) if p else (None, "")


class PreAuthGuard:
    def __init__(self, registry):
        self.registry = registry
        self._requests = {}

    def submit(self, ref, *, payer, procedure, amount_minor, emergency,
               now_h, requested_by):
        if ref in self._requests:
            raise GuardError(f"duplicate pre-auth: {ref}")
        self._requests[ref] = {
            "ref": ref, "payer": payer, "procedure": procedure,
            "amount_minor": amount_minor, "emergency": emergency,
            "submitted_h": now_h, "state": "pending",
            "by": requested_by, "history": [(now_h, "submitted")]}

    def decide(self, ref, *, approved, now_h, by, reason=""):
        r = self._get(ref)
        if r["state"] != "pending":
            raise GuardError(f"{ref} already {r['state']}")
        if not approved and not reason:
            raise GuardError("a denial without a reason is a stonewall")
        r["state"] = "approved" if approved else "denied"
        r["history"].append((now_h, r["state"]))

    def sweep(self, now_h):
        """Escalate / deem-approve everything whose clock ran out.
        Returns the state changes — sweep is the only escalator, so a
        request escalates exactly once."""
        changed = []
        for ref, r in sorted(self._requests.items()):
            if r["state"] != "pending":
                continue
            elapsed = now_h - r["submitted_h"]
            sla = self.registry.sla(r["payer"], r["emergency"])
            dc_hours, basis = self.registry.deemed_consent(r["payer"])
            if dc_hours is not None and elapsed >= dc_hours:
                r["state"] = "deemed-approved"
                r["history"].append((now_h, f"deemed-approved ({basis})"))
                changed.append({"ref": ref, "to": "deemed-approved",
                                "legal_basis": basis})
            elif elapsed >= sla:
                r["state"] = "escalated"
                r["history"].append((now_h, "escalated: SLA breach"))
                changed.append({"ref": ref, "to": "escalated",
                                "sla_hours": sla})
        return changed

    def _get(self, ref):
        r = self._requests.get(ref)
        if r is None:
            raise GuardError(f"unknown pre-auth: {ref}")
        return r

    def report(self):
        by_state = {}
        for r in self._requests.values():
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        return {"total": len(self._requests), "by_state": by_state}
