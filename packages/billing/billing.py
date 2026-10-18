"""Point-of-care billing: itemized bills, deposits, waivers, exemptions.

Nigerian reality: most care is paid out of pocket, and the poorest must not
be turned away. So a Bill supports:
- itemized charges against a facility tariff,
- deposits and part-payments,
- waivers (partial or full, always named-actor + reason),
- exemption categories (under-5, pregnant, elderly, indigent) that zero
  eligible lines automatically,
- insurance split: NHIA/scheme covers a percentage, patient pays the rest —
  the scheme share becomes a claim reference for the adjudicator.

Money is integer kobo throughout — floats and money never mix.
"""

EXEMPTION_CATEGORIES = {"under5", "pregnant", "elderly", "indigent"}


class Error(Exception):
    pass


class Line:
    def __init__(self, code, description, amount_kobo, exemptible=True):
        if amount_kobo < 0:
            raise Error("negative charges are credits — use waive()")
        self.code = code
        self.description = description
        self.amount_kobo = amount_kobo
        self.exemptible = exemptible
        self.waived_kobo = 0
        self.exempted = False

    @property
    def due_kobo(self):
        return 0 if self.exempted else self.amount_kobo - self.waived_kobo


class Bill:
    def __init__(self, bill_id, patient_id, facility_id, episode_id=None,
                 insurance_pct=0):
        if not 0 <= insurance_pct <= 100:
            raise Error("insurance_pct must be 0..100")
        self.id = bill_id
        self.patient_id = patient_id
        self.facility_id = facility_id
        self.episode_id = episode_id
        self.insurance_pct = insurance_pct
        self.lines = []
        self.payments = []   # {"amount_kobo", "method", "received_by", "kind"}
        self.exemptions = set()
        self.state = "open"  # open -> settled | written_off

    # ---- charges
    def add_line(self, code, description, amount_kobo, exemptible=True):
        if self.state != "open":
            raise Error(f"bill {self.id} is {self.state}")
        line = Line(code, description, amount_kobo, exemptible)
        # apply standing exemptions to the new line immediately
        if self.exemptions and exemptible:
            line.exempted = True
        self.lines.append(line)
        return line

    def apply_exemption(self, category, actor):
        if category not in EXEMPTION_CATEGORIES:
            raise Error(f"unknown exemption: {category}")
        if not actor:
            raise Error("exemptions name who granted them")
        self.exemptions.add(category)
        for line in self.lines:
            if line.exemptible:
                line.exempted = True

    def waive(self, amount_kobo, actor, reason):
        if not actor or not reason:
            raise Error("waivers name who and why")
        if amount_kobo <= 0:
            raise Error("waiver amount must be positive")
        remaining = amount_kobo
        for line in self.lines:
            if remaining == 0:
                break
            take = min(line.due_kobo, remaining)
            line.waived_kobo += take
            remaining -= take
        return amount_kobo - remaining  # actually waived

    # ---- money in
    def pay(self, amount_kobo, method, received_by, kind="payment"):
        if self.state != "open":
            raise Error(f"bill {self.id} is {self.state}")
        if amount_kobo <= 0:
            raise Error("payments are positive")
        if kind not in {"deposit", "payment", "refund"}:
            raise Error(f"unknown payment kind: {kind}")
        if not received_by:
            raise Error("payments name the cashier")
        self.payments.append({"amount_kobo": amount_kobo, "method": method,
                              "received_by": received_by, "kind": kind})
        if kind != "deposit" and self.balance_kobo() == 0:
            self.state = "settled"
        return self.balance_kobo()

    # ---- views
    def total_kobo(self):
        return sum(l.amount_kobo for l in self.lines)

    def patient_due_kobo(self):
        """What the patient owes after exemptions and waivers, minus the
        insurer's share of the remainder."""
        due = sum(l.due_kobo for l in self.lines)
        insurer = due * self.insurance_pct // 100
        return due - insurer

    def insurer_due_kobo(self):
        due = sum(l.due_kobo for l in self.lines)
        return due * self.insurance_pct // 100

    def paid_kobo(self):
        return sum(p["amount_kobo"] for p in self.payments if p["kind"] != "refund") \
             - sum(p["amount_kobo"] for p in self.payments if p["kind"] == "refund")

    def balance_kobo(self):
        return max(self.patient_due_kobo() - self.paid_kobo(), 0)

    def receipt(self):
        return {"bill": self.id, "total_kobo": self.total_kobo(),
                "patient_due_kobo": self.patient_due_kobo(),
                "insurer_due_kobo": self.insurer_due_kobo(),
                "paid_kobo": self.paid_kobo(), "balance_kobo": self.balance_kobo(),
                "state": self.state,
                "lines": [{"code": l.code, "amount_kobo": l.amount_kobo,
                           "waived_kobo": l.waived_kobo, "exempted": l.exempted}
                          for l in self.lines]}


class BillingOffice:
    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.bills = {}
        self._seq = 0

    def open_bill(self, patient_id, episode_id=None, insurance_pct=0):
        self._seq += 1
        b = Bill(f"BILL-{self._seq:05d}", patient_id, self.facility_id,
                 episode_id, insurance_pct)
        self.bills[b.id] = b
        return b

    def daily_revenue(self):
        return sum(b.paid_kobo() for b in self.bills.values())

    def outstanding(self):
        return [{"bill": b.id, "patient": b.patient_id, "balance_kobo": b.balance_kobo()}
                for b in self.bills.values() if b.balance_kobo() > 0]

    def write_off(self, bill_id, actor, reason):
        b = self.bills[bill_id]
        if not actor or not reason:
            raise Error("write-offs name who and why")
        b.state = "written_off"
        return b
