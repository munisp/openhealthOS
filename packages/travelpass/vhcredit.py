"""Premium financing — pay-small-small, with vesting instead of vibes.

Coverage vests day by day as installments land: the traveller is
covered for paid days only, stated on the pass. On default the cover
PAUSES at a stated boundary — never a silent lapse, never a
fake-active pass. Catch-up payments resume vesting from the pause.
Fail-closed applied to consumer credit.
"""

CREDIT_VERSION = "vhcredit@1.0.0"


class CreditError(Exception):
    pass


def _refuse(msg):
    raise CreditError(msg)


class InstallmentPlan:
    """A payment plan over a cover window. Vesting is arithmetic:
    paid_minor / per_day_minor = vested days, bounded by the window."""

    def __init__(self, plan_id, *, cover_ref, total_minor, per_day_minor,
                 installments, window_days, currency, day):
        if total_minor <= 0 or per_day_minor <= 0:
            _refuse("a plan prices itself in stated minor units")
        if installments < 2:
            _refuse("one payment is not a plan — pay in full instead")
        if installments > window_days:
            _refuse("more installments than days is a trap, not a plan")
        self.plan_id = plan_id
        self.cover_ref = cover_ref
        self.total_minor = total_minor
        self.per_day_minor = per_day_minor
        self.installments = installments
        self.window_days = window_days
        self.currency = currency
        self.paid_minor = 0
        self.payments = []
        self.paused_at_day = None
        self.created_day = day

    def schedule(self):
        """Even splits; the remainder rides on the LAST installment so
        early payments are round numbers — the receipt shows its math."""
        base = self.total_minor // self.installments
        sched = [base] * self.installments
        sched[-1] += self.total_minor - base * self.installments
        return sched

    def pay(self, amount_minor, *, day, ref):
        if self.paused_at_day is None and self.paid_minor >= self.total_minor:
            _refuse("plan already settled — overpayment is not coverage")
        if amount_minor <= 0:
            _refuse("a zero payment is not a payment")
        if not ref:
            _refuse("a payment without a reference is cash in a pocket")
        if self.paid_minor + amount_minor > self.total_minor:
            _refuse("overpayment refused — the plan total is the ceiling")
        self.paid_minor += amount_minor
        self.payments.append({"amount_minor": amount_minor,
                              "day": day, "ref": ref})
        if self.paused_at_day is not None and \
                self.vested_days() >= self.paused_at_day:
            self.paused_at_day = None      # caught up — vesting resumes
        return self.status(day)

    def vested_days(self):
        return min(self.window_days, self.paid_minor // self.per_day_minor)

    def check_default(self, *, day, due_minor):
        """Missed an installment: pause at the stated boundary. Vested
        days stay vested — the pause is forward-looking, never
        retroactive."""
        if self.paid_minor < due_minor and self.paused_at_day is None:
            self.paused_at_day = self.vested_days()
        return self.status(day)

    def status(self, day):
        vested = self.vested_days()
        settled = self.paid_minor >= self.total_minor
        return {"plan_id": self.plan_id, "cover_ref": self.cover_ref,
                "paid_minor": self.paid_minor,
                "total_minor": self.total_minor,
                "vested_days": vested,
                "state": ("settled" if settled else
                          "paused" if self.paused_at_day is not None
                          else "vesting"),
                "paused_at_day": self.paused_at_day,
                "covered_today": (settled or
                                  (self.paused_at_day is None and
                                   day < vested)),
                "stated": "covered days are vested days — a pause at a "
                          "stated boundary, never a silent lapse"}


class CreditBook:
    def __init__(self):
        self.plans = {}

    def open_plan(self, plan_id, **kw):
        if plan_id in self.plans:
            _refuse("duplicate plan id")
        self.plans[plan_id] = InstallmentPlan(plan_id, **kw)
        return self.plans[plan_id]

    def report(self):
        states = {}
        for p in self.plans.values():
            s = p.status(p.created_day)["state"]
            states[s] = states.get(s, 0) + 1
        return {"version": CREDIT_VERSION, "plans": len(self.plans),
                "by_state": states}
